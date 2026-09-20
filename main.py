import os
from typing import List, Dict, Optional, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from ortools.sat.python import cp_model

app = FastAPI(title="学校時間割最適化 API", version="2.0.0")

# Google Apps Script（GAS）からのAPI呼び出しを許可するCORS設定
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
    target_type: Optional[str] = "teacher"
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
    max_consecutive: Optional[int] = 4
    max_teacher_daily_hours: Optional[int] = 5
    facility_limits: Optional[Dict[str, int]] = {}
    time_limit: Optional[float] = 60.0

# 定数定義
DAYS = ['月', '火', '水', '木', '金']
PERIODS = [1, 2, 3, 4, 5, 6]


# ==========================================
# 2. 最適化エンドポイント (/optimize)
# ==========================================

@app.get("/")
def read_root():
    return {"status": "online", "message": "時間割最適化APIサーバーは正常に稼働しています。"}


@app.post("/optimize")
def optimize_schedule(req: ScheduleRequest):
    model = cp_model.CpModel()

    # --- A. タスク（授業単位）の整理 ---
    tasks = []
    all_classes = set()
    teacher_names = set()
    
    for idx, t in enumerate(req.teachers):
        teacher_names.add(t.name)
        for c in t.classes:
            all_classes.add(c)
            tasks.append({
                "task_id": len(tasks),
                "teacher_id": str(t.id),
                "teacher_name": t.name,
                "subject": t.subject,
                "class_name": c,
                "hours": t.hours
            })

    all_classes = sorted(list(all_classes))

    # 決定変数 x[task_id, day, period] in {0, 1}
    x = {}
    for task in tasks:
        tid = task["task_id"]
        for d in DAYS:
            for p in PERIODS:
                x[tid, d, p] = model.NewBoolVar(f"x_{tid}_{d}_{p}")

    # --- B. 基本制約 ---

    # 1. 各授業の週コマ数を割り当てる
    for task in tasks:
        tid = task["task_id"]
        model.Add(sum(x[tid, d, p] for d in DAYS for p in PERIODS) == task["hours"])

    # 2. クラスの重複禁止（同一クラス・同一コマは最大1授業）
    for c in all_classes:
        c_tasks = [t["task_id"] for t in tasks if t["class_name"] == c]
        for d in DAYS:
            for p in PERIODS:
                model.Add(sum(x[tid, d, p] for tid in c_tasks) <= 1)

    # 3. 教員の重複禁止（同一教員・同一コマは最大1授業）
    for t_name in teacher_names:
        t_tasks = [t["task_id"] for t in tasks if t["teacher_name"] == t_name]
        for d in DAYS:
            for p in PERIODS:
                model.Add(sum(x[tid, d, p] for tid in t_tasks) <= 1)

    # 4. 同教科の同日重複禁止（同じクラスで同じ教科は1日に1コマまで）
    subjects = list(set(t["subject"] for t in tasks))
    for c in all_classes:
        for d in DAYS:
            for subj in subjects:
                subj_tasks = [t["task_id"] for t in tasks if t["class_name"] == c and t["subject"] == subj]
                if subj_tasks:
                    model.Add(sum(x[tid, d, p] for tid in subj_tasks for p in PERIODS) <= 1)

    # --- C. 追加制約（今回の新条件） ---

    # 5. 教員の1日最大コマ数制限（デフォルト5コマ＝最低1コマ空き）
    max_daily = req.max_teacher_daily_hours if req.max_teacher_daily_hours is not None else 5
    for t_name in teacher_names:
        t_tasks = [t["task_id"] for t in tasks if t["teacher_name"] == t_name]
        for d in DAYS:
            model.Add(sum(x[tid, d, p] for tid in t_tasks for p in PERIODS) <= max_daily)

    # 6. 施設利用上限（例: 体育館同時3コマ、理科室1コマなど）
    if req.facility_limits:
        for subj, limit in req.facility_limits.items():
            if limit > 0:
                subj_tasks = [t["task_id"] for t in tasks if t["subject"] == subj]
                if subj_tasks:
                    for d in DAYS:
                        for p in PERIODS:
                            model.Add(sum(x[tid, d, p] for tid in subj_tasks) <= limit)

    # 7. NGコマ設定（教員の出勤不可・授業不可コマ）
    if req.ng_list:
        for ng in req.ng_list:
            ng_tid_str = str(ng.target_id)
            for task in tasks:
                if str(task["teacher_id"]) == ng_tid_str or task["teacher_name"] == ng_tid_str:
                    if ng.day in DAYS and ng.period in PERIODS:
                        model.Add(x[task["task_id"], ng.day, ng.period] == 0)

    # 8. 学年一斉コマ設定（道徳＝1限、学活・総合＝6限など）
    if req.group_slots:
        for gs in req.group_slots:
            grade_str = str(gs.grade).replace('年', '')
            for c in all_classes:
                if c.startswith(f"{grade_str}-"):
                    target_tasks = [t["task_id"] for t in tasks if t["class_name"] == c and t["subject"] == gs.subject]
                    if target_tasks and gs.day in DAYS and gs.period in PERIODS:
                        # 指定の曜日・時限に確実に配置する
                        model.Add(sum(x[tid, gs.day, gs.period] for tid in target_tasks) == 1)

    # --- D. 最適化ソルバーの実行 ---
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = req.time_limit or 60.0

    status = solver.Solve(model)

    # --- E. 結果返却 ---
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        schedule = []
        for task in tasks:
            tid = task["task_id"]
            for d in DAYS:
                for p in PERIODS:
                    if solver.Value(x[tid, d, p]) == 1:
                        schedule.append({
                            "class": task["class_name"],
                            "day": d,
                            "period": p,
                            "subject": task["subject"],
                            "teacher_name": task["teacher_name"],
                            "teacher_id": task["teacher_id"]
                        })
        return {"status": "success", "schedule": schedule}
    else:
        raise HTTPException(
            status_code=400, 
            detail="条件を満たす時間割が見つかりませんでした。NG設定や担当コマ数などの制約が厳しすぎる可能性があります。"
        )

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
