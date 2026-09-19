from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from ortools.sat.python import cp_model

app = FastAPI()

# --- リクエストデータの型定義 ---
class Teacher(BaseModel):
    id: int
    name: str
    subject: str
    classes: List[str]  # 例: ["1-1", "1-2"]
    hours: int

class NGItem(BaseModel):
    teacher_id: int
    day: str
    period: str

class Options(BaseModel):
    max_per_day: Optional[int] = 1
    max_consecutive: Optional[int] = 2
    balance_days: Optional[bool] = True

class RequestData(BaseModel):
    teachers: List[Teacher]
    ngList: Optional[List[NGItem]] = []
    options: Optional[Options] = Options()

DAYS = ['月', '火', '水', '木', '金']
PERIODS = [1, 2, 3, 4, 5, 6]

@app.post("/optimize")
def optimize_timetable(data: RequestData):
    model = cp_model.CpModel()
    
    teachers = data.teachers
    ng_list = data.ngList or []
    opts = data.options or Options()

    # --- 1. 変数定義 ---
    # x[(t_id, class_name, d, p)] = 1 (授業あり) / 0 (授業なし)
    x = {}
    for t in teachers:
        for c in t.classes:
            for d in range(len(DAYS)):
                for p in range(len(PERIODS)):
                    x[(t.id, c, d, p)] = model.NewBoolVar(f'x_{t.id}_{c}_{d}_{p}')

    # --- 2. 必須（ハード）制約 ---

    # (A) 各教科・各クラスの指定コマ数を満たす
    for t in teachers:
        for c in t.classes:
            model.Add(
                sum(x[(t.id, c, d, p)] for d in range(len(DAYS)) for p in range(len(PERIODS))) == t.hours
            )

    # (B) 教員の重複禁止（教員名 t.name で集約して同コマ重複を防ぐ）
    teacher_name_map: Dict[str, List[tuple]] = {}
    teacher_total_hours: Dict[str, int] = {}

    for t in teachers:
        if t.name not in teacher_name_map:
            teacher_name_map[t.name] = []
            teacher_total_hours[t.name] = 0
        
        teacher_total_hours[t.name] += t.hours * len(t.classes)
        for c in t.classes:
            teacher_name_map[t.name].append((t.id, c))

    for t_name, tc_list in teacher_name_map.items():
        for d in range(len(DAYS)):
            for p in range(len(PERIODS)):
                # 同一教員は同じ曜日・時限に最大1コマ（例：佐藤先生の国語と道徳が重ならない）
                model.Add(
                    sum(x[(t_id, c, d, p)] for (t_id, c) in tc_list) <= 1
                )

    # (C) クラスの重複禁止（1つのクラスに同じコマで2つ以上の授業が入らない）
    class_teachers_map: Dict[str, List[tuple]] = {}
    for t in teachers:
        for c in t.classes:
            if c not in class_teachers_map:
                class_teachers_map[c] = []
            class_teachers_map[c].append((t.id, c))

    for c, tc_list in class_teachers_map.items():
        for d in range(len(DAYS)):
            for p in range(len(PERIODS)):
                model.Add(
                    sum(x[(t_id, cls_name, d, p)] for (t_id, cls_name) in tc_list) <= 1
                )

    # (D) NG設定（不可曜日・時限の適用）
    day_map = {d: i for i, d in enumerate(DAYS)}
    for ng in ng_list:
        if ng.day in day_map:
            d_idx = day_map[ng.day]
            p_str = str(ng.period).replace('限', '').replace('時限', '').strip()
            if p_str.isdigit():
                p_idx = int(p_str) - 1
                if 0 <= p_idx < len(PERIODS):
                    for t in teachers:
                        if t.id == ng.teacher_id:
                            for c in t.classes:
                                model.Add(x[(t.id, c, d_idx, p_idx)] == 0)

    # --- 3. 調整（ソフト）制約 ---

    # (E) 1日あたりの同一教科上限（各クラスで同じ教科は1日 max_per_day コマまで）
    max_per_day = opts.max_per_day or 1
    for c, tc_list in class_teachers_map.items():
        subj_map: Dict[str, List[tuple]] = {}
        for t_id, cls_name in tc_list:
            t_obj = next(t for t in teachers if t.id == t_id)
            if t_obj.subject not in subj_map:
                subj_map[t_obj.subject] = []
            subj_map[t_obj.subject].append((t_id, cls_name))
            
        for subj, list_pairs in subj_map.items():
            for d in range(len(DAYS)):
                model.Add(
                    sum(x[(t_id, cls_name, d, p)] for (t_id, cls_name) in list_pairs for p in range(len(PERIODS))) <= max_per_day
                )

    # (F) 3コマ以上連続授業の禁止（生徒側の負担軽減）
    max_consecutive = opts.max_consecutive or 2
    for c, tc_list in class_teachers_map.items():
        for d in range(len(DAYS)):
            for p in range(len(PERIODS) - max_consecutive):
                model.Add(
                    sum(x[(t_id, cls_name, d, p + k)] for (t_id, cls_name) in tc_list for k in range(max_consecutive + 1)) <= max_consecutive
                )

    # (G) 曜日ごとのコマ数平準化
    if opts.balance_days:
        for t_name, tc_list in teacher_name_map.items():
            tot_h = teacher_total_hours[t_name]
            max_daily_limit = (tot_h + len(DAYS) - 1) // len(DAYS) + 1
            for d in range(len(DAYS)):
                model.Add(
                    sum(x[(t_id, c, d, p)] for (t_id, c) in tc_list for p in range(len(PERIODS))) <= max_daily_limit
                )

        daily_totals = []
        for d in range(len(DAYS)):
            day_total = model.NewIntVar(0, len(teachers) * len(PERIODS), f'day_total_{d}')
            model.Add(day_total == sum(x[(t.id, c, d, p)] for t in teachers for c in t.classes for p in range(len(PERIODS))))
            daily_totals.append(day_total)

        max_day_load = model.NewIntVar(0, len(teachers) * len(PERIODS), 'max_day_load')
        min_day_load = model.NewIntVar(0, len(teachers) * len(PERIODS), 'min_day_load')

        for d in range(len(DAYS)):
            model.Add(daily_totals[d] <= max_day_load)
            model.Add(daily_totals[d] >= min_day_load)

        model.Minimize(max_day_load - min_day_load)

    # --- 4. 実行・解答取得 ---
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20.0  # 探索タイムアウト 20秒
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        timetable = []
        for t in teachers:
            for c in t.classes:
                for d_idx, day_name in enumerate(DAYS):
                    for p_idx, p_num in enumerate(PERIODS):
                        if solver.Value(x[(t.id, c, d_idx, p_idx)]) == 1:
                            timetable.append({
                                "teacher": t.name,
                                "subject": t.subject,
                                "class": c,
                                "day": day_name,
                                "period": p_num
                            })
        return {
            "status": "SUCCESS",
            "timetable": timetable
        }
    elif status == cp_model.INFEASIBLE:
        return {
            "status": "INFEASIBLE",
            "message": "条件を満たす時間割が存在しません。各クラスの合計コマ数が週30コマを超えていないか、NG設定を緩和して再試行してください。"
        }
    else:
        return {
            "status": "ERROR",
            "message": "探索時間内に解が見つかりませんでした。"
        }
