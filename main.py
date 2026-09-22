from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict
from ortools.sat.python import cp_model
import json

app = FastAPI(title="学校時間割最適化 API", version="2.2.9")

# ========== 教科マスタ（固定） ==========
SUBJECT_MASTER = {
    '1年': {'国語': 4, '社会': 4, '数学': 4, '理科': 3, '英語': 3, '音楽': 1, '美術': 1, '保体': 1, '技術': 1, '家庭': 1, '学活': 1, '総合': 1, '道徳': 1},
    '2年': {'国語': 4, '社会': 4, '数学': 4, '理科': 3, '英語': 3, '音楽': 1, '美術': 1, '保体': 1, '技術': 1, '家庭': 1, '学活': 1, '総合': 1, '道徳': 1},
    '3年': {'国語': 4, '社会': 4, '数学': 4, '理科': 3, '英語': 3, '音楽': 1, '美術': 1, '保体': 1, '技術': 1, '家庭': 1, '学活': 1, '総合': 1, '道徳': 1},
}

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
    return {"message": "学校時間割最適化API v2.2.9"}

@app.post("/optimize")
def optimize_schedule(request: ScheduleRequest):
    """
    時間割最適化エンドポイント
    
    修正内容（v2.2.9）:
    - 同一教科の連続配置を禁止
    - 各クラスの教科の多様性を確保
    - 教科マスタに基づいた配置コマ数の厳密な管理
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
        
        # 各クラスの必要教科コマ数を計算
        class_subject_hours = {}
        for class_name in all_classes:
            grade_match = class_name[0]  # "1-1" -> "1"
            grade_key = f"{grade_match}年"
            
            if grade_key in SUBJECT_MASTER:
                class_subject_hours[class_name] = SUBJECT_MASTER[grade_key].copy()
            else:
                class_subject_hours[class_name] = {}
        
        print(f"DEBUG: class_subject_hours={class_subject_hours}")
        
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
        
        # ========== 制約2: 各教科の必要コマ数 ==========
        for class_name in all_classes:
            for subject, required_hours in class_subject_hours[class_name].items():
                subject_vars = [
                    var for key, var in assignment_vars.items()
                    if key.startswith(f"{class_name}_") and f"_{subject}_" in key
                ]
                
                if subject_vars and required_hours > 0:
                    model.Add(sum(subject_vars) == required_hours)
        
        # ========== 制約3: 同一教科の連続配置を禁止 ==========
        for class_name in all_classes:
            for day in days:
                for period in range(1, 6):  # 6番目の時限の場合は5番目を見ない（6限がない場合もあるため）
                    if day in short_days and period == 6:
                        continue
                    
                    current_slot_vars = {}
                    next_slot_vars = {}
                    
                    for key, var in assignment_vars.items():
                        if key.startswith(f"{class_name}_{day}_{period}_"):
                            # subject_teacherを抽出
                            parts = key.split("_")
                            subject = parts[3]
                            current_slot_vars[subject] = var
                        elif key.startswith(f"{class_name}_{day}_{period + 1}_"):
                            parts = key.split("_")
                            subject = parts[3]
                            next_slot_vars[subject] = var
                    
                    # 同じ教科なら両方が1にはならない
                    for subject in set(current_slot_vars.keys()) & set(next_slot_vars.keys()):
                        model.Add(current_slot_vars[subject] + next_slot_vars[subject] <= 1)
        
        # ========== 制約4: 各教員の週コマ数制限 ==========
        for teacher in request.teachers:
            teacher_vars = [
                var for key, var in assignment_vars.items()
                if f"_{teacher.id}" in key
            ]
            
            if teacher_vars:
                model.Add(sum(teacher_vars) <= teacher.hours)
        
        # ========== 制約5: NG時間帯 ==========
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
        
        # ========== 制約6: 施設上限 ==========
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
        
        # ========== 制約7: 学年一斉コマ ==========
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
        
        # クラスごとの教科統計を出力
        for class_name in all_classes:
            subject_counts = {}
            for item in schedule:
                if item["class"] == class_name:
                    subj = item["subject"]
                    subject_counts[subj] = subject_counts.get(subj, 0) + 1
            print(f"DEBUG: {class_name}={subject_counts}")
        
        print(f"DEBUG: generated_schedule_count={len(schedule)}")
        
        return {
            "schedule": schedule,
            "status": "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
            "message": f"時間割生成完了: {len(schedule)}コマ配置"
        }
    
    except Exception as e:
        print(f"ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "schedule": [],
            "status": "ERROR",
            "message": f"エラー: {str(e)}"
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
