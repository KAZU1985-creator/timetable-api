import os
import re
from collections import defaultdict
from typing import List, Dict, Optional, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from ortools.sat.python import cp_model

app = FastAPI(title="学校時間割最適化 API", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 1. リクエスト・レスポンスのデータモデル定義
# ==========================================

class TeacherAssignment(BaseModel):
    id: Any
    name: str
    subject: str
    classes: List[str]
    hours: int

class NGItem(BaseModel):
    target_type: Optional[str] = "teacher"   # "teacher" or "class"
    target_id: Any
    day: str
    period: int

class GroupSlot(BaseModel):
    grade: str
    day: str
    period: int
    subject: str

class ScheduleRequest(BaseModel):
    teachers: List[TeacherAssignment]
    ng_list: Optional[List[NGItem]] = []
    group_slots: Optional[List[GroupSlot]] = []
    short_days: Optional[List[str]] = []          # 【追加】5時間授業の曜日（6限なし）
    require_full: Optional[bool] = True           # 【追加】全クラスの全コマを埋める
    max_consecutive: Optional[int] = 4
    max_teacher_daily_hours: Optional[int] = 5
    facility_limits: Optional[Dict[str, int]] = {}
    time_limit: Optional[float] = 60.0

DAYS = ['月', '火', '水', '木', '金']
PERIODS = [1, 2, 3, 4, 5, 6]


# ==========================================
# 2. 事前診断（ソルバーを回す前に「数え間違い」を検出）
# ==========================================

def _nat_key(name):
    return [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', str(name))]


def _grade_label(cls_list):
    """['1-1','1-2',...] -> '1年(1-1〜1-10 計10クラス)'"""
    cls_list = sorted(cls_list, key=_nat_key)
    g = cls_list[0].split('-')[0]
    return f"{g}年({cls_list[0]}〜{cls_list[-1]} 計{len(cls_list)}クラス)"


def diagnose(req, tasks, all_classes, short_set, class_blocked, teacher_ng):
    """必ず解が存在しなくなる矛盾を、原因つきで返す。"""
    errors = []
    real_slots = [(d, p) for d in DAYS for p in PERIODS if not (d in short_set and p == 6)]

    # 0. 一斉コマが存在しないコマ（5時間授業日の6限）に置かれている
    for gs in (req.group_slots or []):
        if gs.day in short_set and gs.period == 6:
            errors.append(f"一斉コマ設定: {gs.grade}年 {gs.day}曜6限の「{gs.subject}」は、"
                          f"5時間授業の日で6限が存在しません。別の時限に移してください。")

    # 1. クラスごとの「授業コマ数」と「空きコマ数」の一致
    groups = defaultdict(list)
    for c in all_classes:
        need = sum(t["hours"] for t in tasks if t["class_name"] == c)
        free = sum(1 for (d, p) in real_slots if (c, d, p) not in class_blocked)
        if need > free or (req.require_full and need < free):
            groups[(c.split('-')[0], need, free)].append(c)
    for (g, need, free), cl in sorted(groups.items()):
        diff = free - need
        if diff > 0:
            msg = f"空きコマが{diff}コマ余ります（授業{need}コマ / 空き{free}コマ）。教科マスタの時数か、一斉コマ設定（総合など）が不足しています。"
        else:
            msg = f"授業が{-diff}コマ入りきりません（授業{need}コマ / 空き{free}コマ）。"
        errors.append(f"クラス時数: {_grade_label(cl)} {msg}")

    # 2. 施設上限：教科の総コマ数 ≦ 上限 × 週の時限数
    for subj, limit in (req.facility_limits or {}).items():
        if limit <= 0:
            continue
        need = sum(t["hours"] for t in tasks if t["subject"] == subj)
        cap = limit * len(real_slots)
        if need > cap:
            min_limit = -(-need // len(real_slots))
            errors.append(f"施設上限: 「{subj}」は週{need}コマ必要ですが、同時上限{limit}×{len(real_slots)}コマ={cap}コマしか置けません。"
                          f"上限を{min_limit}以上にしてください。")

    # 3. 教員ごとの担当コマ数 ≦ 使える時間枠（NG・1日上限を考慮）
    max_daily = req.max_teacher_daily_hours if req.max_teacher_daily_hours is not None else 5
    by_teacher = defaultdict(int)
    for t in tasks:
        by_teacher[t["teacher_name"]] += t["hours"]
    tid_of = {t["teacher_name"]: t["teacher_id"] for t in tasks}
    for name, need in by_teacher.items():
        ngs = teacher_ng.get(tid_of[name], set()) | teacher_ng.get(name, set())
        cap = 0
        for d in DAYS:
            avail = sum(1 for p in PERIODS if (d, p) in set(real_slots) and (d, p) not in ngs)
            cap += min(avail, max_daily)
        if need > cap:
            errors.append(f"教員負担: {name} 先生は週{need}コマ担当ですが、NG・1日上限を除くと{cap}コマまでしか入りません。")

    # 4. 同一クラス・同一教科は1日1コマまで → 週5コマ超は不可能
    for t in tasks:
        if t["hours"] > len(DAYS):
            errors.append(f"同教科同日制限: {t['class_name']}の{t['subject']}が週{t['hours']}コマ（最大5コマ）")
    return errors


# ==========================================
# 3. 最適化エンドポイント (/optimize)
# ==========================================

@app.get("/")
def read_root():
    return {"status": "online", "message": "時間割最適化APIサーバーは正常に稼働しています。", "version": "2.1.0"}


@app.post("/optimize")
def optimize_schedule(req: ScheduleRequest):
    model = cp_model.CpModel()
    short_set = {d for d in (req.short_days or []) if d in DAYS}

    # --- A. タスク整理 ---
    tasks, all_classes, teacher_names = [], set(), set()
    for t in req.teachers:
        teacher_names.add(t.name)
        for c in t.classes:
            all_classes.add(c)
            tasks.append({"task_id": len(tasks), "teacher_id": str(t.id), "teacher_name": t.name,
                          "subject": t.subject, "class_name": c, "hours": t.hours})
    all_classes = sorted(all_classes, key=_nat_key)

    # --- B. NGの仕分け（target_type を必ず見る） ---
    teacher_ng = defaultdict(set)        # 教員ID/名前 -> {(曜日,時限)}
    class_blocked = set()                # (クラス, 曜日, 時限) 授業を置けないコマ
    for ng in (req.ng_list or []):
        if ng.day not in DAYS or ng.period not in PERIODS:
            continue
        if (ng.target_type or "teacher") == "class":
            class_blocked.add((str(ng.target_id), ng.day, ng.period))
        else:
            teacher_ng[str(ng.target_id)].add((ng.day, ng.period))

    # 学年一斉コマ：そのコマの授業がエンジン外で確定しているなら、クラスをブロック扱いにする
    forced = []                          # (対象タスクID群, 曜日, 時限) エンジン内で固定するもの
    for gs in (req.group_slots or []):
        g = str(gs.grade).replace('年', '')
        if gs.day not in DAYS or gs.period not in PERIODS:
            continue
        for c in all_classes:
            if c.startswith(f"{g}-"):
                ids = [t["task_id"] for t in tasks if t["class_name"] == c and t["subject"] == gs.subject]
                if ids:
                    forced.append((ids, gs.day, gs.period))
                else:
                    class_blocked.add((c, gs.day, gs.period))

    # --- C. 事前診断：矛盾があれば、解く前に原因を返す ---
    errors = diagnose(req, tasks, all_classes, short_set, class_blocked, teacher_ng)
    if errors:
        detail = "設定に矛盾があるため時間割を作成できません。\n" + "\n".join(f"{i+1}. {e}" for i, e in enumerate(errors[:20]))
        if len(errors) > 20:
            detail += f"\n…ほか{len(errors)-20}件"
        raise HTTPException(status_code=400, detail=detail)

    # --- D. 決定変数 ---
    x = {}
    for task in tasks:
        for d in DAYS:
            for p in PERIODS:
                x[task["task_id"], d, p] = model.NewBoolVar(f"x_{task['task_id']}_{d}_{p}")

    # 1. 各授業の週コマ数
    for task in tasks:
        model.Add(sum(x[task["task_id"], d, p] for d in DAYS for p in PERIODS) == task["hours"])

    # 2. クラスの重複禁止 ＋ 空欄禁止
    for c in all_classes:
        c_tasks = [t["task_id"] for t in tasks if t["class_name"] == c]
        for d in DAYS:
            for p in PERIODS:
                s = sum(x[tid, d, p] for tid in c_tasks)
                if (d in short_set and p == 6) or (c, d, p) in class_blocked:
                    model.Add(s == 0)            # 存在しない/ロック済みのコマには置かない
                elif req.require_full:
                    model.Add(s == 1)            # それ以外は必ず1コマ埋める
                else:
                    model.Add(s <= 1)

    # 3. 教員の重複禁止
    teacher_slot = {}
    for name in teacher_names:
        t_tasks = [t["task_id"] for t in tasks if t["teacher_name"] == name]
        for d in DAYS:
            for p in PERIODS:
                teacher_slot[name, d, p] = sum(x[tid, d, p] for tid in t_tasks)
                model.Add(teacher_slot[name, d, p] <= 1)

    # 4. 同クラス・同教科は1日1コマまで
    subjects = list({t["subject"] for t in tasks})
    for c in all_classes:
        for d in DAYS:
            for subj in subjects:
                ids = [t["task_id"] for t in tasks if t["class_name"] == c and t["subject"] == subj]
                if ids:
                    model.Add(sum(x[tid, d, p] for tid in ids for p in PERIODS) <= 1)

    # 5. 教員の1日最大コマ数
    max_daily = req.max_teacher_daily_hours if req.max_teacher_daily_hours is not None else 5
    for name in teacher_names:
        for d in DAYS:
            model.Add(sum(teacher_slot[name, d, p] for p in PERIODS) <= max_daily)

    # 5b. 【追加】教員の連続授業制限（max_consecutive を実際に使う）
    mc = req.max_consecutive or 0
    if 1 <= mc < len(PERIODS):
        for name in teacher_names:
            for d in DAYS:
                for start in range(1, len(PERIODS) - mc + 1):
                    model.Add(sum(teacher_slot[name, d, p] for p in range(start, start + mc + 1)) <= mc)

    # 6. 施設上限
    for subj, limit in (req.facility_limits or {}).items():
        if limit > 0:
            ids = [t["task_id"] for t in tasks if t["subject"] == subj]
            if ids:
                for d in DAYS:
                    for p in PERIODS:
                        model.Add(sum(x[tid, d, p] for tid in ids) <= limit)

    # 7. 教員NG（教員IDまたは名前で一致）
    for task in tasks:
        keys = teacher_ng.get(task["teacher_id"], set()) | teacher_ng.get(task["teacher_name"], set())
        for (d, p) in keys:
            model.Add(x[task["task_id"], d, p] == 0)

    # 8. 学年一斉コマで、対象教科のタスクがエンジン内にある場合はそのコマに固定
    for ids, d, p in forced:
        model.Add(sum(x[tid, d, p] for tid in ids) == 1)

    # --- E. 実行 ---
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = req.time_limit or 60.0
    # CPUコアが少ない環境（Render無料枠など）でも、複数の探索戦略を並走させた方が速く解けることが多い
    solver.parameters.num_workers = int(os.environ.get("SOLVER_WORKERS", "8"))
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        schedule = []
        for task in tasks:
            for d in DAYS:
                for p in PERIODS:
                    if solver.Value(x[task["task_id"], d, p]) == 1:
                        schedule.append({"class": task["class_name"], "day": d, "period": p,
                                         "subject": task["subject"], "teacher_name": task["teacher_name"],
                                         "teacher_id": task["teacher_id"]})
        return {"status": "success", "solver_status": solver.StatusName(status), "schedule": schedule}

    if status == cp_model.UNKNOWN:
        msg = "制限時間内に解が見つかりませんでした。time_limitを延ばすか、サーバーの性能を上げてください。"
    else:
        msg = ("事前診断では数え間違いは見つかりませんでしたが、条件の組み合わせで解がありません。"
               "教員NG・1日上限・連続授業制限・施設上限・同教科同日禁止のどれかを緩めてください。")
    raise HTTPException(status_code=400, detail=msg)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
