from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from ortools.sat.python import cp_model

app = FastAPI()

class TeacherInput(BaseModel):
    id: int
    name: str
    subject: str
    classes: List[str]
    hours: int

class NGInput(BaseModel):
    target_type: str = "teacher"
    target_id: str
    day: str
    period: int

class OptimizeRequest(BaseModel):
    teachers: List[TeacherInput]
    ng_list: Optional[List[NGInput]] = []
    max_consecutive: Optional[int] = 4
    time_limit: Optional[float] = 60.0

@app.post("/optimize")
def optimize_schedule(req: OptimizeRequest):
    model = cp_model.CpModel()
    
    days = ['月', '火', '水', '木', '金']
    periods = [1, 2, 3, 4, 5, 6]
    
    # 全クラス一覧の抽出
    all_classes = sorted(list(set(c for t in req.teachers for c in t.classes)))
    
    # 変数定義: x[t_idx, c, d, p] in {0, 1}
    x = {}
    for t_idx, t in enumerate(req.teachers):
        for c in t.classes:
            for d in days:
                for p in periods:
                    # 金曜6コマ目は一律除外（授業なし固定）
                    if d == '金' and p == 6:
                        continue
                    x[t_idx, c, d, p] = model.NewBoolVar(f'x_{t_idx}_{c}_{d}_{p}')
                    
    # 1. 各教員の担当コマ数の厳格遵守
    for t_idx, t in enumerate(req.teachers):
        for c in t.classes:
            valid_vars = [
                x[t_idx, c, d, p]
                for d in days for p in periods
                if (t_idx, c, d, p) in x
            ]
            model.Add(sum(valid_vars) == t.hours)

    # 2. 同一クラス・同一日時の重複禁止（1クラス1時限に1授業まで）
    for c in all_classes:
        for d in days:
            for p in periods:
                vars_in_slot = [
                    x[t_idx, c, d, p]
                    for t_idx, t in enumerate(req.teachers)
                    if c in t.classes and (t_idx, c, d, p) in x
                ]
                model.Add(sum(vars_in_slot) <= 1)

    # 3. 教員のダブルブッキング絶対禁止（同一教員・同一日時に1クラスまで）
    teacher_ids = list(set(t.id for t in req.teachers))
    for tid in teacher_ids:
        for d in days:
            for p in periods:
                vars_for_teacher = [
                    x[t_idx, c, d, p]
                    for t_idx, t in enumerate(req.teachers)
                    if t.id == tid
                    for c in t.classes
                    if (t_idx, c, d, p) in x
                ]
                model.Add(sum(vars_for_teacher) <= 1)

    # 4. ★【最重要】同日同一教科の重複・連続を絶対禁止（1日1コマまで）
    all_subjects = list(set(t.subject for t in req.teachers))
    for c in all_classes:
        for d in days:
            for subj in all_subjects:
                vars_same_subj = [
                    x[t_idx, c, d, p]
                    for t_idx, t in enumerate(req.teachers)
                    if t.subject == subj and c in t.classes
                    for p in periods
                    if (t_idx, c, d, p) in x
                ]
                model.Add(sum(vars_same_subj) <= 1)

    # 5. NG設定の遵守
    for ng in req.ng_list:
        try:
            ng_tid = int(ng.target_id)
        except ValueError:
            continue
        d = ng.day
        p = ng.period
        for t_idx, t in enumerate(req.teachers):
            if t.id == ng_tid:
                for c in t.classes:
                    if (t_idx, c, d, p) in x:
                        model.Add(x[t_idx, c, d, p] == 0)

    # 6. 教員の連続コマ数制限（指定コマ数を超える連続を禁止）
    if req.max_consecutive and req.max_consecutive > 0:
        max_c = req.max_consecutive
        for tid in teacher_ids:
            for d in days:
                for p in range(1, 6 - max_c + 1):
                    window_vars = []
                    for window_p in range(p, p + max_c + 1):
                        if window_p <= 6:
                            for t_idx, t in enumerate(req.teachers):
                                if t.id == tid:
                                    for c in t.classes:
                                        if (t_idx, c, d, window_p) in x:
                                            window_vars.append(x[t_idx, c, d, window_p])
                    if len(window_vars) > max_c:
                        model.Add(sum(window_vars) <= max_c)

    # ソルバーの実行
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = req.time_limit or 60.0
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        schedule = []
        for (t_idx, c, d, p), var in x.items():
            if solver.Value(var) == 1:
                t = req.teachers[t_idx]
                schedule.append({
                    "teacher_id": t.id,
                    "teacher_name": t.name,
                    "subject": t.subject,
                    "class": c,
                    "day": d,
                    "period": p
                })
        return {"status": "success", "schedule": schedule}
    else:
        raise HTTPException(
            status_code=400, 
            detail="条件を満たす時間割が見つかりませんでした。教員コマ数やNG設定を緩めて再お試しください。"
        )
