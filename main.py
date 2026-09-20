from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from ortools.sat.python import cp_model

app = FastAPI(title="週29コマ・連続コマ柔軟制御 時間割API")

class TeacherAssignment(BaseModel):
    id: int
    name: str
    subject: str
    classes: List[str]
    hours: int

class NGRule(BaseModel):
    target_type: str
    target_id: str
    day: str
    period: int

class TimetableRequest(BaseModel):
    teachers: List[TeacherAssignment]
    ng_list: Optional[List[NGRule]] = []
    max_consecutive: Optional[int] = 4  # デフォルトを「4コマ連続までOK」に緩和
    time_limit: Optional[float] = 60.0

@app.post("/optimize")
def optimize_timetable(req: TimetableRequest):
    DAYS = ["月", "火", "水", "木", "金"]
    PERIODS = [1, 2, 3, 4, 5, 6]
    
    # 金曜6限を除外した29スロットを定義
    SLOTS = [(d, p) for d in DAYS for p in PERIODS if not (d == "金" and p == 6)]

    model = cp_model.CpModel()
    
    teacher_map = {}
    classes_set = set()
    tasks = []
    task_counter = 0
    
    for t in req.teachers:
        teacher_map[t.id] = t.name
        for c in t.classes:
            classes_set.add(c)
            for _ in range(t.hours):
                tasks.append({
                    "task_id": task_counter,
                    "teacher_id": t.id,
                    "teacher_name": t.name,
                    "subject": t.subject,
                    "class_name": c
                })
                task_counter += 1

    classes_list = sorted(list(classes_set))
    
    # 決定変数の作成
    x = {}
    for task in tasks:
        for d, p in SLOTS:
            x[(task["task_id"], d, p)] = model.NewBoolVar(f"x_{task['task_id']}_{d}_{p}")

    # 制約1: 全タスクを確実にいずれかのスロットに1つ割り当てる
    for task in tasks:
        model.Add(sum(x[(task["task_id"], d, p)] for d, p in SLOTS) == 1)

    # 制約2: 同一クラスのコマ重複禁止
    for c in classes_list:
        for d, p in SLOTS:
            c_tasks = [x[(t["task_id"], d, p)] for t in tasks if t["class_name"] == c]
            model.Add(sum(c_tasks) <= 1)

    # 制約3: 同一教員のコマ重複禁止
    for tid in teacher_map.keys():
        for d, p in SLOTS:
            t_tasks = [x[(t["task_id"], d, p)] for t in tasks if t["teacher_id"] == tid]
            model.Add(sum(t_tasks) <= 1)

    # 制約4: 同一クラスで同一教科は1日1コマまで（連続2コマ等の特別授業除く）
    for c in classes_list:
        c_subjects = set(t["subject"] for t in tasks if t["class_name"] == c)
        for subj in c_subjects:
            for d in DAYS:
                day_slots = [p for (d_s, p) in SLOTS if d_s == d]
                subj_tasks = [x[(t["task_id"], d, p)] for t in tasks if t["class_name"] == c and t["subject"] == subj and p in day_slots]
                model.Add(sum(subj_tasks) <= 1)

    # ★ 制約5: 教員の連続コマ数制限（max_consecutive=4 の場合、5コマ以上の連続授業を禁止）
    max_c = req.max_consecutive if req.max_consecutive else 4
    window_size = max_c + 1  # 4コマ許容なら 5コマ幅のウィンドウでチェック

    for tid in teacher_map.keys():
        for d in DAYS:
            day_periods = sorted([p for (d_s, p) in SLOTS if d_s == d])
            # 連続する時限のウィンドウを取得
            for i in range(len(day_periods) - window_size + 1):
                window_p = day_periods[i:i + window_size]
                if window_p[-1] - window_p[0] == window_size - 1:
                    window_tasks = []
                    for p in window_p:
                        window_tasks.extend([x[(t["task_id"], d, p)] for t in tasks if t["teacher_id"] == tid])
                    model.Add(sum(window_tasks) <= max_c)

    # 制約6: NGコマの回避
    if req.ng_list:
        for ng in req.ng_list:
            t_id = int(ng.target_id) if ng.target_id.isdigit() else None
            if t_id in teacher_map:
                for t in tasks:
                    if t["teacher_id"] == t_id and (ng.day, ng.period) in SLOTS:
                        model.Add(x[(t["task_id"], ng.day, ng.period)] == 0)

    # ソルバー実行
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = req.time_limit
    
    status = solver.Solve(model)
    
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        schedule = []
        for t in tasks:
            for d, p in SLOTS:
                if solver.Value(x[(t["task_id"], d, p)]) == 1:
                    schedule.append({
                        "teacher_id": t["teacher_id"],
                        "teacher_name": t["teacher_name"],
                        "subject": t["subject"],
                        "class": t["class_name"],
                        "day": d,
                        "period": p
                    })
        return {"status": "SUCCESS", "schedule": schedule}
    else:
        raise HTTPException(
            status_code=400, 
            detail="条件を満たす解が見つかりませんでした。教員設定やNG指定をご確認ください。"
        )
