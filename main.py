from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Dict, Any
from ortools.sat.python import cp_model

app = FastAPI()

# 入力データの型定義
class InputData(BaseModel):
    teachers: List[Dict[str, Any]]
    ngList: List[Dict[str, Any]]

@app.get("/")
def home():
    return {"status": "OK", "message": "時間割最適化APIが正常に動作しています"}

@app.post("/optimize")
def solve_timetable(data: InputData):
    model = cp_model.CpModel()
    
    days = ["月", "火", "水", "木", "金"]
    periods = [1, 2, 3, 4, 5, 6]
    slots = [(d, p) for d in days for p in periods]
    
    teachers = data.teachers
    ng_list = data.ngList
    
    # 担当クラスの全リストを抽出
    all_classes = set()
    for t in teachers:
        for c in t.get("classes", []):
            all_classes.add(c)
    all_classes = sorted(list(all_classes))

    if not all_classes:
        return {"status": "ERROR", "message": "クラス情報が見つかりません"}

    # 変数定義: x[(class, teacher_id, slot_idx)]
    x = {}
    for c in all_classes:
        for t in teachers:
            for s_idx in range(len(slots)):
                x[(c, t["id"], s_idx)] = model.NewBoolVar(f'x_{c}_{t["id"]}_{s_idx}')

    # 制約1: 各クラス・各コマには最大1人の教員
    for c in all_classes:
        for s_idx in range(len(slots)):
            model.Add(sum(x[(c, t["id"], s_idx)] for t in teachers) <= 1)

    # 制約2: 教員は同じ時間に複数クラスを担当できない
    for t in teachers:
        for s_idx in range(len(slots)):
            model.Add(sum(x[(c, t["id"], s_idx)] for c in all_classes) <= 1)

    # 制約3: 定例NGコマの適用
    for ng in ng_list:
        t_id = ng.get("teacher_id")
        ng_day = ng.get("day")
        ng_period = str(ng.get("period", ""))
        
        for s_idx, (d, p) in enumerate(slots):
            if d == ng_day and ("終日不可" in ng_period or f"{p}限" in ng_period):
                for c in all_classes:
                    if (c, t_id, s_idx) in x:
                        model.Add(x[(c, t_id, s_idx)] == 0)

    # 【重要追加】制約4: 各担当授業の指定コマ数（hours）を確実に割り当てる
    for t in teachers:
        target_hours = t.get("hours", 4)
        for c in t.get("classes", []):
            model.Add(sum(x[(c, t["id"], s_idx)] for s_idx in range(len(slots))) == target_hours)

    # ソルバーの実行（制限時間30秒）
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30.0
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        timetable_result = []
        for s_idx, (d, p) in enumerate(slots):
            for c in all_classes:
                for t in teachers:
                    if solver.Value(x[(c, t["id"], s_idx)]) == 1:
                        timetable_result.append({
                            "day": d,
                            "period": p,
                            "class": c,
                            "teacher": t["name"],
                            "subject": t.get("subject", "")
                        })
        return {"status": "SUCCESS", "timetable": timetable_result}
    else:
        return {"status": "INFEASIBLE", "message": "条件を満たす時間割が見つかりませんでした"}
