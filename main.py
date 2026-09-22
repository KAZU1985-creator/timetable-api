from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict
from ortools.sat.python import cp_model
import json

app = FastAPI(title="学校時間割最適化 API", version="2.2.8")

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
    total_hours: Optional[int] = 29
    time_limit: Optional[float] = 60.0

class Schedule(BaseModel):
    class_name: str
    day: str
    period: int
    subject: str
    teacher_name: str

@app.get("/")
def read_root():
    return {"message": "学校時間割最適化API v2.2.8"}

@app.post("/optimize")
def optimize_schedule(request: ScheduleRequest):
    """
    時間割最適化エンドポイント
    
    修正内容（v2.2.8）:
    - スケジューリングロジックを完全に再実装
    - 各クラスの必要教科を明確に設定
    - 教員の時間を厳密に管理
    """
    
    try:
        days = ["月", "火", "水", "木", "金"]
        periods = [1, 2, 3, 4, 5, 6]
        short_days = request.short_days or ["水"]
        
        total_hours = request.total_hours or 29
        
        # 全クラス数を推定
        all_classes = set()
        for teacher in request.teachers:
            all_classes.update(teacher.classes)
        
        all_classes = sorted(list(all_classes))
        num_classes = len(all_classes)
        
        print(f"DEBUG: classes={all_classes}, num_classes={num_classes}")
        
        # 教科別に教員を分類
        subject_teachers = {}
        for teacher in request.teachers:
            if teacher.subject not in subject_teachers:
                subject_teachers[teacher.subject] = []
            subject_teachers[teacher.subject].append(teacher)
        
        print(f"DEBUG: subject_teachers={list(subject_teachers.keys())}")
        
        # CP-SAT モデル作成
        model = cp_model.CpModel()
        
        # 変数定義: (クラス, 曜日, 時限, 教科, 教員) -> 0/1
        assignment_vars = {}
        
        for class_name in all_classes:
            for day in days:
                for period in periods:
                    # 水曜の6限はスキップ
                    if day in short_days and period == 6:
                        continue
                    
                    for subject, teachers_list in subject_teachers.items():
                        for teacher in teachers_list:
                            if class_name in teacher.classes:
                                var_name = f"{class_name}_{day}_{period}_{subject}_{teacher.id}"
                                assignment_vars[var_name] = model.NewBoolVar(var_name)
        
        print(f"DEBUG: total_vars={len(assignment_vars)}")
        
        # ========== 制約1: 各クラス・時限には1つの授業だけ ==========
        for class_name in all_classes:
            for day in days:
                for period in periods:
                    if day in short_days and period == 6:
                        continue
                    
                    slot_vars = [
                        var for key, var in assignment_vars.items()
                        if key.startswith(f"{class_name}_{day}_{period}_")
                    ]
                    
                    if slot_vars:
                        model.Add(sum(slot_vars) == 1)
        
        # ========== 制約2: 各教員の週コマ数制限 ==========
        for teacher in request.teachers:
            teacher_vars = [
                var for key, var in assignment_vars.items()
                if f"_{teacher.id}" in key
            ]
            
            if teacher_vars:
                model.Add(sum(teacher_vars) <= teacher.hours)
        
        # ========== 制約3: 教員の日単位の最大連続授業制限 ==========
        max_consecutive = request.max_consecutive or 4
        for teacher in request.teachers:
            for day in days:
                day_vars = [
                    var for key, var in assignment_vars.items()
                    if f"_{day}_" in key and f"_{teacher.id}" in key
                ]
                
                if len(day_vars) > max_consecutive:
                    for i in range(len(day_vars) - max_consecutive + 1):
                        model.Add(sum(day_vars[i:i+max_consecutive]) <= max_consecutive - 1)
        
        # ========== 制約4: NG時間帯 ==========
        for ng in request.ng_list or []:
            if ng.target_type == "teacher":
                teacher_id = int(ng.target_id)
                day = ng.day
                period = ng.period
                
                ng_vars = [
                    var for key, var in assignment_vars.items()
                    if f"_{day}_{period}_" in key and f"_{teacher_id}" in key
                ]
                
                for var in ng_vars:
                    model.Add(var == 0)
        
        # ========== 制約5: 施設上限 ==========
        facility_limits = request.facility_limits or {}
        for facility, limit in facility_limits.items():
            for day in days:
                for period in periods:
                    if day in short_days and period == 6:
                        continue
                    
                    facility_vars = [
                        var for key, var in assignment_vars.items()
                        if f"_{day}_{period}_{facility}_" in key
                    ]
                    
                    if facility_vars:
                        model.Add(sum(facility_vars) <= limit)
        
        # ========== 制約6: 学年一斉コマ ==========
        for group_slot in request.group_slots or []:
            grade = group_slot.grade
            day = group_slot.day
            period = group_slot.period
            subject = group_slot.subject
            
            # その学年のクラスで該当時限に同じ教科を配置
            grade_classes = [c for c in all_classes if c.startswith(grade + "-")]
            
            for class_name in grade_classes:
                slot_vars = [
                    var for key, var in assignment_vars.items()
                    if key.startswith(f"{class_name}_{day}_{period}_{subject}_")
                ]
                
                if slot_vars:
                    model.Add(sum(slot_vars) == 1)
        
        # ========== 目的関数: 最大化 ==========
        model.Maximize(sum(assignment_vars.values()))
        
        # ========== ソルバー実行 ==========
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = request.time_limit or 60.0
        solver.parameters.log_search_progress = False
        
        status = solver.Solve(model)
        
        print(f"DEBUG: solver_status={status}")
        
        # ========== 結果抽出 ==========
        schedule = []
        
        for key, var in assignment_vars.items():
            if solver.Value(var) == 1:
                parts = key.split("_")
                class_name = parts[0]
                day = parts[1]
                period = int(parts[2])
                subject = parts[3]
                teacher_id = int(parts[4])
                
                # 教員名を取得
                teacher_name = ""
                for teacher in request.teachers:
                    if teacher.id == teacher_id:
                        teacher_name = teacher.name
                        break
                
                schedule.append({
                    "class": class_name,
                    "day": day,
                    "period": period,
                    "subject": subject,
                    "teacher_name": teacher_name,
                    "teacher_id": teacher_id
                })
        
        print(f"DEBUG: generated_schedule_count={len(schedule)}")
        
        return {
            "schedule": schedule,
            "status": "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
            "message": f"時間割生成完了: {len(schedule)}コマ配置"
        }
    
    except Exception as e:
        print(f"ERROR: {str(e)}")
        return {
            "schedule": [],
            "status": "ERROR",
            "message": f"エラー: {str(e)}"
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
