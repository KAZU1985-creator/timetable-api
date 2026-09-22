from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict
from ortools.sat.python import cp_model
import json

app = FastAPI(title="学校時間割最適化 API", version="2.2.3")

class TeacherAssignment(BaseModel):
    id: int
    name: str
    subject: str
    classes: List[str]
    hours: int

class NGItem(BaseModel):
    target_type: str
    target_id: str
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
    short_days: Optional[List[str]] = []
    require_full: Optional[bool] = True
    max_consecutive: Optional[int] = 4
    max_teacher_daily_hours: Optional[int] = 5
    facility_limits: Optional[Dict[str, int]] = {}
    total_hours: Optional[int] = 29  # ← 週あたりのコマ数（全クラス共通）
    time_limit: Optional[float] = 60.0

class Schedule(BaseModel):
    class_name: str
    day: str
    period: int
    subject: str
    teacher_name: str

@app.get("/")
def read_root():
    return {"message": "学校時間割最適化API v2.2.3"}

@app.post("/optimize")
def optimize_schedule(request: ScheduleRequest):
    """
    時間割最適化エンドポイント
    
    修正内容（v2.2.3）:
    - real_slots計算を total_hours から直接計算
    - 教員配置コマ数ではなく、要求コマ数を優先
    """
    
    try:
        days = ["月", "火", "水", "木", "金"]
        periods = [1, 2, 3, 4, 5, 6]
        short_days = request.short_days or ["水"]
        
        # ★★★ 修正1: total_hours を基準に real_slots を計算 ★★★
        total_hours = request.total_hours or 29
        
        # 全クラス数を推定（教員配置から）
        all_classes = set()
        for teacher in request.teachers:
            all_classes.update(teacher.classes)
        
        num_classes = len(all_classes) if all_classes else 9
        
        # 1学年 = total_hours
        # 全学年 = total_hours × 学年数
        grade_count = 3
        total_required_slots = total_hours * grade_count
        
        # 実際に埋められるスロット数
        short_day_periods = (len(periods) - 1) * len([d for d in days if d in short_days])
        normal_day_periods = len(periods) * len([d for d in days if d not in short_days])
        real_slots = (short_day_periods + normal_day_periods) * num_classes
        
        print(f"DEBUG: total_hours={total_hours}, num_classes={num_classes}")
        print(f"DEBUG: total_required_slots={total_required_slots}, real_slots={real_slots}")
        
        # ★★★ 修正2: real_slots が不足する場合は警告だけで続行 ★★★
        if real_slots < total_required_slots:
            print(f"WARNING: 必要スロット({total_required_slots}) > 利用可能スロット({real_slots})")
            # エラーを出さずに続行
        
        # CP-SAT モデル作成
        model = cp_model.CpModel()
        
        # 変数定義
        assignment_vars = {}
        for teacher_idx, teacher in enumerate(request.teachers):
            for class_name in teacher.classes:
                for day in days:
                    for period in periods:
                        # 短い曜日の6限は禁止
                        if day in short_days and period == 6:
                            continue
                        
                        key = (teacher_idx, class_name, day, period)
                        assignment_vars[key] = model.NewBoolVar(f'assign_{teacher_idx}_{class_name}_{day}_{period}')
        
        # 制約1: 各教員の総配置時間を制限
        for teacher_idx, teacher in enumerate(request.teachers):
            total_var = []
            for class_name in teacher.classes:
                for day in days:
                    for period in periods:
                        if day in short_days and period == 6:
                            continue
                        key = (teacher_idx, class_name, day, period)
                        if key in assignment_vars:
                            total_var.append(assignment_vars[key])
            
            if total_var:
                model.Add(sum(total_var) <= teacher.hours)
        
        # 制約2: 各教員の日単位の最大連続授業制限
        max_consecutive = request.max_consecutive or 4
        for teacher_idx, teacher in enumerate(request.teachers):
            for day in days:
                day_vars = []
                for period in periods:
                    if day in short_days and period == 6:
                        continue
                    key = (teacher_idx, teacher.classes[0], day, period)
                    if key in assignment_vars:
                        day_vars.append(assignment_vars[key])
                
                if len(day_vars) > max_consecutive:
                    for i in range(len(day_vars) - max_consecutive + 1):
                        model.Add(sum(day_vars[i:i+max_consecutive]) <= max_consecutive - 1)
        
        # 制約3: NG時間帯
        for ng in request.ng_list or []:
            if ng.target_type == "teacher":
                teacher_idx = int(ng.target_id) - 1
                if 0 <= teacher_idx < len(request.teachers):
                    teacher = request.teachers[teacher_idx]
                    for class_name in teacher.classes:
                        key = (teacher_idx, class_name, ng.day, ng.period)
                        if key in assignment_vars:
                            model.Add(assignment_vars[key] == 0)
        
        # 制約4: 施設上限
        facility_limits = request.facility_limits or {}
        for facility, limit in facility_limits.items():
            for day in days:
                for period in periods:
                    if day in short_days and period == 6:
                        continue
                    
                    facility_vars = []
                    for teacher_idx, teacher in enumerate(request.teachers):
                        if teacher.subject == facility:
                            for class_name in teacher.classes:
                                key = (teacher_idx, class_name, day, period)
                                if key in assignment_vars:
                                    facility_vars.append(assignment_vars[key])
                    
                    if facility_vars:
                        model.Add(sum(facility_vars) <= limit)
        
        # 制約5: 学年一斉コマ（group_slots）
        for group_slot in request.group_slots or []:
            grade = group_slot.grade
            day = group_slot.day
            period = group_slot.period
            subject = group_slot.subject
            
            group_vars = []
            for teacher_idx, teacher in enumerate(request.teachers):
                if teacher.subject == subject:
                    for class_name in teacher.classes:
                        if class_name.startswith(grade + '-'):
                            key = (teacher_idx, class_name, day, period)
                            if key in assignment_vars:
                                group_vars.append(assignment_vars[key])
            
            if group_vars:
                model.Add(sum(group_vars) == len([c for c in set([t.classes for t in request.teachers]) 
                                                      if any(cn.startswith(grade + '-') for cn in [t.classes for t in request.teachers])]))
        
        # ★★★ 修正3: 目的関数（最大化）★★★
        objective_vars = list(assignment_vars.values())
        if objective_vars:
            model.Maximize(sum(objective_vars))
        
        # ソルバー実行
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = request.time_limit or 60.0
        solver.parameters.log_search_progress = False
        
        status = solver.Solve(model)
        
        if status not in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            return {
                "schedule": [],
                "status": "NO_SOLUTION",
                "message": "実行可能な解が見つかりませんでした（制約が厳しすぎます）"
            }
        
        # 結果抽出
        schedule = []
        for (teacher_idx, class_name, day, period), var in assignment_vars.items():
            if solver.Value(var) == 1:
                teacher = request.teachers[teacher_idx]
                schedule.append({
                    "class": class_name,
                    "day": day,
                    "period": period,
                    "subject": teacher.subject,
                    "teacher_name": teacher.name,
                    "teacher_id": teacher.id
                })
        
        return {
            "schedule": schedule,
            "status": "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
            "message": f"時間割生成完了: {len(schedule)}コマ配置"
        }
    
    except Exception as e:
        return {
            "schedule": [],
            "status": "ERROR",
            "message": f"エラー: {str(e)}"
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
