from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from ortools.sat.python import cp_model

app = FastAPI()

# --- 入力データ構造の定義 ---
class Teacher(BaseModel):
    id: int
    name: str
    subject: str
    classes: List[str]
    hours: int

class NGItem(BaseModel):
    teacher_id: int
    day: str
    period: str

class Options(BaseModel):
    max_per_day: Optional[int] = 1        # 1日あたりの同一教科・同一クラス上限コマ数（基本1コマ）
    max_consecutive: Optional[int] = 2    # 最大連続コマ数（3コマ以上の連続を禁止）
    balance_days: Optional[bool] = True   # 曜日分散（平準化）の有効化

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
    # x[(t_id, d, p)] = 1 (教員t_idが曜日d, 時限pに授業を行う) / 0 (行わない)
    x = {}
    for t in teachers:
        for d in range(len(DAYS)):
            for p in range(len(PERIODS)):
                x[(t.id, d, p)] = model.NewBoolVar(f'x_{t.id}_{d}_{p}')

    # --- 2. 基本ハード制約 ---
    
    # (A) 各教員の週必要コマ数を厳密に満たす
    for t in teachers:
        model.Add(sum(x[(t.id, d, p)] for d in range(len(DAYS)) for p in range(len(PERIODS))) == t.hours)

    # (B) 同一クラスの重複禁止（同じクラスは同じ曜日・時限に1コマのみ）
    class_teachers: Dict[str, List[Teacher]] = {}
    for t in teachers:
        for c in t.classes:
            if c not in class_teachers:
                class_teachers[c] = []
            class_teachers[c].append(t)

    for c, t_list in class_teachers.items():
        for d in range(len(DAYS)):
            for p in range(len(PERIODS)):
                model.Add(sum(x[(t.id, d, p)] for t in t_list) <= 1)

    # (C) NG設定の適用
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
                            model.Add(x[(t.id, d_idx, p_idx)] == 0)

    # ==========================================
    # ★ 改善対策①：1日あたりの同一教科上限制約
    # ==========================================
    max_per_day = opts.max_per_day or 1
    for c, t_list in class_teachers.items():
        # クラスごとに教科別でグループ化
        subj_teachers: Dict[str, List[Teacher]] = {}
        for t in t_list:
            if t.subject not in subj_teachers:
                subj_teachers[t.subject] = []
            subj_teachers[t.subject].append(t)
            
        for subj, st_list in subj_teachers.items():
            for d in range(len(DAYS)):
                # 1つのクラスにおいて、同一教科は1日あたり max_per_day コマまで
                model.Add(
                    sum(x[(t.id, d, p)] for t in st_list for p in range(len(PERIODS))) <= max_per_day
                )

    # ==========================================
    # ★ 改善対策②：連続コマ制限（3コマ以上連続の禁止）
    # ==========================================
    max_consecutive = opts.max_consecutive or 2
    for c, t_list in class_teachers.items():
        for d in range(len(DAYS)):
            # 任意の連続する (max_consecutive + 1) コマの和を max_consecutive 以下にする
            for p in range(len(PERIODS) - max_consecutive):
                model.Add(
                    sum(x[(t.id, d, p + k)] for t in t_list for k in range(max_consecutive + 1)) <= max_consecutive
                )

    # ==========================================
    # ★ 改善対策③：曜日ごとのコマ数分散（平準化）
    # ==========================================
    if opts.balance_days:
        # 教員ごとの1日あたり最大コマ数の上限（例: 週4〜5コマなら1日最大2コマ）
        for t in teachers:
            max_daily_limit = (t.hours + len(DAYS) - 1) // len(DAYS) + 1
            for d in range(len(DAYS)):
                model.Add(
                    sum(x[(t.id, d, p)] for p in range(len(PERIODS))) <= max_daily_limit
                )

        # 全体の曜日別コマ数の最大値と最小値の差（偏り）を最小化（Minimax最適化）
        daily_totals = []
        for d in range(len(DAYS)):
            day_total = model.NewIntVar(0, len(teachers) * len(PERIODS), f'day_total_{d}')
            model.Add(day_total == sum(x[(t.id, d, p)] for t in teachers for p in range(len(PERIODS))))
            daily_totals.append(day_total)

        max_day_load = model.NewIntVar(0, len(teachers) * len(PERIODS), 'max_day_load')
        min_day_load = model.NewIntVar(0, len(teachers) * len(PERIODS), 'min_day_load')

        for d in range(len(DAYS)):
            model.Add(daily_totals[d] <= max_day_load)
            model.Add(daily_totals[d] >= min_day_load)

        # 目的関数: 最大日と最小日のコマ数差を最小化し、月〜金へ均等に分配する
        model.Minimize(max_day_load - min_day_load)

    # --- 3. ソルバー実行 ---
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 15.0  # 探索時間の上限（15秒）
    status = solver.Solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        timetable = []
        for t in teachers:
            for d_idx, day_name in enumerate(DAYS):
                for p_idx, p_num in enumerate(PERIODS):
                    if solver.Value(x[(t.id, d_idx, p_idx)]) == 1:
                        timetable.append({
                            "teacher": t.name,
                            "subject": t.subject,
                            "class": t.classes[0] if t.classes else "",
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
            "message": "制約条件を満たす時間割が見つかりませんでした。NG設定や条件を緩めて再試行してください。"
        }
    else:
        return {
            "status": "ERROR",
            "message": "探索時間内に解が見つかりませんでした。"
        }
