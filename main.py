from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from ortools.sat.python import cp_model

app = FastAPI(title="時間割自動生成API")

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
    max_consecutive: Optional[int] = 2  # 教員の最大連続授業数（デフォルト2コマまで）
    time_limit: Optional[float] = 30.0  # 探索制限時間（秒）

@app.post("/optimize")
def optimize_schedule(req: OptimizeRequest):
    model = cp_model.CpModel()
    teachers = req.teachers
    
    # 1. 変数定義 x[teacher_id, class_name, day, period]
    x = {}
    for t in teachers:
        for c in t.classes:
            for day in DAYS:
                for p in PERIODS:
                    x[(t.id, c, day, p)] = model.NewBoolVar(f"x_{t.id}_{c}_{day}_{p}")

    # 2. ハード制約: 各教員・各クラスの必要コマ数を満たす
    for t in teachers:
        for c in t.classes:
            model.Add(
                sum(x[(t.id, c, day, p)] for day in DAYS for p in PERIODS) == t.hours
            )

    # 3. ハード制約: 教員の重複禁止（同一時間帯に1つのクラスのみ）
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

    # 4. ハード制約: クラスの重複禁止（同一時間帯に1つの授業のみ）
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

    # 5. 運用制約: 1日1教科1コマまで（同一クラス）
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

    # 6. 運用制約: 教員の連続授業制限（デフォルト3コマ以上連続を禁止）
    max_c = req.max_consecutive or 2
    for tid, cls_list in teacher_classes_map.items():
        for day in DAYS:
            for p_idx in range(len(PERIODS) - max_c):
                target_periods = PERIODS[p_idx : p_idx + max_c + 1]
                model.Add(
                    sum(x[(tid, c, day, p)] for c in cls_list for p in target_periods) <= max_c
                )

    # 7. ハード制約: NG枠の適用
    for ng in req.ng_list:
        if ng.day in DAYS and ng.period in PERIODS:
            if ng.target_type == 'teacher':
                try:
                    tid = int(ng.target_id)
                    if tid in teacher_classes_map:
                        for c in teacher_classes_map[tid]:
                            model.Add(x[(tid, c, ng.day, ng.period)] == 0)
                except ValueError:
                    pass
            elif ng.target_type == 'class':
                c = ng.target_id
                if c in class_teachers_map:
                    for t in class_teachers_map[c]:
                        model.Add(x[(t.id, c, ng.day, ng.period)] == 0)

    # ソルバーの実行
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = req.time_limit or 30.0
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
        raise HTTPException(
            status_code=400,
            detail="条件を満たす時間割が存在しません。各クラスの合計コマ数が30を超えていないか、NG設定やコマ数制限を緩めて再試行してください。"
        )
