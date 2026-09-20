from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional, Any
from ortools.sat.python import cp_model
import re

app = FastAPI(title="時間割自動生成API (GAS完全対応版)")

DAYS = ['月', '火', '水', '木', '金']
PERIODS = [1, 2, 3, 4, 5, 6]

class TeacherInput(BaseModel):
    id: int
    name: str
    subject: str
    classes: List[str]
    hours: int

class NgInput(BaseModel):
    teacher_id: Any
    day: str
    period: Any

class OptionsInput(BaseModel):
    max_per_day: Optional[int] = 1
    balance_days: Optional[bool] = True
    max_consecutive: Optional[int] = 3

class OptimizeRequest(BaseModel):
    teachers: List[TeacherInput]
    ngList: Optional[List[NgInput]] = []
    options: Optional[OptionsInput] = None
    time_limit: Optional[float] = 30.0

def parse_period(p_val: Any) -> Optional[int]:
    """ '1限' や 1 などの入力から数字の 1~6 を抽出 """
    if isinstance(p_val, int):
        return p_val
    m = re.search(r'\d+', str(p_val))
    if m:
        return int(m.group())
    return None

@app.post("/optimize")
def optimize_schedule(req: OptimizeRequest):
    model = cp_model.CpModel()
    teachers = req.teachers
    
    # オプションの取得（GASからの max_consecutive=3 を読み込む）
    max_c = 3
    if req.options and req.options.max_consecutive is not None:
        max_c = req.options.max_consecutive

    # 1. 変数定義 x[t_id, class, day, period]
    x = {}
    for t in teachers:
        for c in t.classes:
            for day in DAYS:
                for p in PERIODS:
                    x[(t.id, c, day, p)] = model.NewBoolVar(f"x_{t.id}_{c}_{day}_{p}")

    # (1) 各教員・クラスのコマ数割り当て
    for t in teachers:
        for c in t.classes:
            model.Add(
                sum(x[(t.id, c, day, p)] for day in DAYS for p in PERIODS) == t.hours
            )

    # (2) 教員の同時間帯重複の禁止
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

    # (5) 教員の連続授業数制限（デフォルト3コマ連続まで許可）
    for tid, cls_list in teacher_classes_map.items():
        for day in DAYS:
            for p_idx in range(len(PERIODS) - max_c):
                target_periods = PERIODS[p_idx : p_idx + max_c + 1]
                model.Add(
                    sum(x[(tid, c, day, p)] for c in cls_list for p in target_periods) <= max_c
                )

    # (6) NG枠制限（GASの ngList 構造に対応）
    for ng in req.ngList:
        ng_day = ng.day.replace('曜日', '').replace('曜', '').trim() if isinstance(ng.day, str) else ng.day
        ng_p = parse_period(ng.period)
        
        if ng_day in DAYS and ng_p in PERIODS:
            try:
                tid = int(ng.teacher_id)
                if tid in teacher_classes_map:
                    for c in teacher_classes_map[tid]:
                        model.Add(x[(tid, c, ng_day, ng_p)] == 0)
            except ValueError:
                pass

    # ソルバーの実行
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = req.time_limit or 30.0
    status = solver.Solve(model)

    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        timetable = []
        for t in teachers:
            for c in t.classes:
                for day in DAYS:
                    for p in PERIODS:
                        if solver.Value(x[(t.id, c, day, p)]) == 1:
                            timetable.append({
                                "teacher": t.name,
                                "subject": t.subject,
                                "day": day,
                                "period": p,
                                "class": c
                            })
        # GASが期待する JSON 構造（status と timetable）で返却
        return {"status": "SUCCESS", "timetable": timetable}
    else:
        return {
            "status": "INFEASIBLE", 
            "message": "条件を満たす時間割を作成できませんでした。担当コマ数が多すぎるか、NG設定が厳しすぎる可能性があります。"
        }
