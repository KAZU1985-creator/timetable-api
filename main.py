from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from ortools.sat.python import cp_model

app = FastAPI(title="時間割自動生成API (29コマ対応版)")

DAYS = ['月', '火', '水', '木', '金']
PERIODS = [1, 2, 3, 4, 5, 6]

class TeacherInput(BaseModel):
    id: int
    name: str
    subject: str
    classes: List[str]
    hours: int  # 1クラスあたりの週コマ数

class NgSlot(BaseModel):
    target_type: str  # 'teacher' または 'class'
    target_id: str    # 教員ID(str) または クラス名
    day: str          # '月', '火', ...
    period: int       # 1 ~ 6

class OptimizeRequest(BaseModel):
    teachers: List[TeacherInput]
    ng_list: Optional[List[NgSlot]] = []
    max_consecutive: Optional[int] = 2  # 教員の最大連続授業数（デフォルト2）
    time_limit: Optional[float] = 30.0  # タイムアウト時間（秒）

@app.post("/optimize")
def optimize_schedule(req: OptimizeRequest):
    model = cp_model.CpModel()
    teachers = req.teachers
    
    # 変数定義 x[t_id, class, day, period]
    x = {}
    for t in teachers:
        for c in t.classes:
            for d, day in enumerate(DAYS):
                for p_idx, p in enumerate(PERIODS):
                    x[(t.id, c, day, p)] = model.NewBoolVar(f"x_{t.id}_{c}_{day}_{p}")

    # (1) 各教員・クラスのコマ数割り当て
    for t in teachers:
        for c in t.classes:
            model.Add(
                sum(x[(t.id, c, day, p)] for day in DAYS for p in PERIODS) == t.hours
            )

    # (2) 教員の同時間帯重複の禁止 (同一教員IDをすべて集約)
    teacher_classes_map = {}
    for t in teachers:
        if t.id not in teacher_classes_map:
            teacher_classes_map[t.id] = []
        for c in t.classes:
            teacher_classes_map[t.id].append(c)

    for tid, cls_list in teacher_classes_map.items():
        for day in DAYS:
            for p in PERIODS:
                model.Add(
                    sum(x[(tid, c, day, p)] for c in cls_list) <= 1
                )

    # (3) クラスの同時間帯重複の禁止
    class_teachers_map = {}
    for t in teachers:
        for c in t.classes:
            if c not in class_teachers_map:
                class_teachers_map[c] = []
            class_teachers_map[c].append(t)

    for c, t_list in class_teachers_map.items():
        for day in DAYS:
            for p in PERIODS:
                model.Add(
                    sum(x[(t.id, c, day, p)] for t in t_list) <= 1
                )

    # (4) 1日1教科1コマまで（同一クラス）
    for c, t_list in class_teachers_map.items():
        subj_teachers = {}
        for t in t_list:
            if t.subject not in subj_teachers:
                subj_teachers[t.subject] = []
            subj_teachers[t.subject].append(t.id)

        for subj, t_ids in subj_teachers.items():
            for day in DAYS:
                model.Add(
                    sum(x[(tid, c, day, p)] for tid in t_ids for p in PERIODS) <= 1
                )

    # (5) 教員の連続授業数制限（例：3コマ連続禁止）
    max_c = req.max_consecutive
    for tid, cls_list in teacher_classes_map.items():
        for day in DAYS:
            for p_idx in range(len(PERIODS) - max_c):
                target_periods = PERIODS[p_idx : p_idx + max_c + 1]
                model.Add(
                    sum(x[(tid, c, day, p)] for c in cls_list for p in target_periods) <= max_c
                )

    # (6) NG枠制限
    for ng in req.ng_list:
        if ng.day in DAYS and ng.period in PERIODS:
            if ng.target_type == 'teacher':
                tid = int(ng.target_id)
                if tid in teacher_classes_map:
                    for c in teacher_classes_map[tid]:
                        model.Add(x[(tid, c, ng.day, ng.period)] == 0)
            elif ng.target_type == 'class':
                c = ng.target_id
                if c in class_teachers_map:
                    for t in class_teachers_map[c]:
                        model.Add(x[(t.id, c, ng.day, ng.period)] == 0)

    # ソルバーの実行
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = req.time_limit
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        schedule = []
        for t in teachers:
            for c in t.classes:
                for day in DAYS:
                    for p in PERIODS:
                        if solver.Value(x[(t.id, c, day, p)]) == 1:
                            schedule.append({
                                "class": c,
                                "day": day,
                                "period": p,
                                "subject": t.subject,
                                "teacher_id": t.id,
                                "teacher_name": t.name
                            })
        return {"status": "SUCCESS", "schedule": schedule}
    else:
        raise HTTPException(status_code=400, detail="条件を満たす時間割を作成できませんでした。制約を緩和してください。")
