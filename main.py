from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional, Dict
import random
import copy

app = FastAPI(title="学校時間割最適化 API", version="2.2.15")

# ========== 教科マスタ（固定） ==========
SUBJECT_MASTER = {
    '1年': {'国語': 4, '社会': 4, '数学': 4, '理科': 3, '英語': 3, '音楽': 1, '美術': 1, '保体': 1, '技術': 1, '家庭': 1, '学活': 1, '総合': 1, '道徳': 1},
    '2年': {'国語': 4, '社会': 4, '数学': 4, '理科': 3, '英語': 3, '音楽': 1, '美術': 1, '保体': 1, '技術': 1, '家庭': 1, '学活': 1, '総合': 1, '道徳': 1},
    '3年': {'国語': 4, '社会': 4, '数学': 4, '理科': 3, '英語': 3, '音楽': 1, '美術': 1, '保体': 1, '技術': 1, '家庭': 1, '学活': 1, '総合': 1, '道徳': 1},
}

# ========== 特別活動（道徳・学活・総合） ==========
SPECIAL_ACTIVITIES = ['道徳', '学活', '総合']

# ========== 技能教科（優先度付き・少ないコマ順） ==========
SKILL_SUBJECTS = ['音楽', '美術', '家庭', '技術', '保体', '理科']

# ========== 基礎5教科（優先度付き・少ないコマ順） ==========
CORE_SUBJECTS = ['英語', '数学', '社会', '国語']

class TeacherAssignment(BaseModel):
    id: int
    name: str
    subject: str
    classes: List[str]
    hours: int
    
    class Config:
        use_enum_values = True

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

@app.get("/")
def read_root():
    return {"message": "学校時間割最適化API v2.2.15（複数試行 + 優先度付き配置版）"}

def sort_slots_by_day(valid_slots):
    """曜日優先度: 金 > 木 > 水 > 火 > 月"""
    day_priority = {"金": 5, "木": 4, "水": 3, "火": 2, "月": 1}
    return sorted(valid_slots, key=lambda x: (-day_priority.get(x[0], 0), x[1]))

def optimize_schedule_single(normalized_teachers, ng_set, facility_limits, group_slots_by_time, short_days):
    """
    1回の試行を実行して、時間割を返す
    """
    days = ["月", "火", "水", "木", "金"]
    periods = [1, 2, 3, 4, 5, 6]
    
    # 全クラスを取得
    all_classes = set()
    for teacher in normalized_teachers:
        all_classes.update(teacher['classes'])
    all_classes = sorted(list(all_classes))
    
    # 教科別に教員を分類（各クラスごと）
    subject_teachers = {}
    for teacher in normalized_teachers:
        if teacher['subject'] not in subject_teachers:
            subject_teachers[teacher['subject']] = []
        
        hours_per_class = teacher['hours'] // len(teacher['classes']) if teacher['classes'] else teacher['hours']
        
        for class_name in teacher['classes']:
            subject_teachers[teacher['subject']].append({
                'id': teacher['id'],
                'name': teacher['name'],
                'subject': teacher['subject'],
                'class': class_name,
                'original_classes': teacher['classes'],
                'total_hours': teacher['hours'],
                'max_hours_this_class': hours_per_class
            })
    
    # 各クラスの時間割グリッドを初期化
    class_timetable = {}
    for class_name in all_classes:
        class_timetable[class_name] = {}
        for day in days:
            class_timetable[class_name][day] = {}
            for period in periods:
                if day in short_days and period == 6:
                    class_timetable[class_name][day][period] = "BLOCKED"
                else:
                    class_timetable[class_name][day][period] = None
    
    # 教員の使用時間数を追跡
    teacher_hours_used = {}
    teacher_class_hours_used = {}
    for teacher in normalized_teachers:
        teacher_hours_used[teacher['id']] = 0
        for class_name in teacher['classes']:
            teacher_class_hours_used[(teacher['id'], class_name)] = 0
    
    # ========== ステップ1: 特別活動を配置 ==========
    for class_name in all_classes:
        grade = class_name[0]
        grade_key = f"{grade}年"
        required_subjects = SUBJECT_MASTER.get(grade_key, {})
        
        for subject in SPECIAL_ACTIVITIES:
            if subject not in required_subjects:
                continue
            
            for (target_grade, target_day, target_period), target_subject in group_slots_by_time.items():
                if target_grade != grade or target_subject != subject:
                    continue
                
                if class_timetable[class_name][target_day][target_period] is not None:
                    continue
                
                available_teacher = None
                if subject in subject_teachers:
                    for teacher_entry in subject_teachers[subject]:
                        if teacher_entry['class'] != class_name:
                            continue
                        
                        if (teacher_entry['id'], target_day, target_period) in ng_set:
                            continue
                        
                        if teacher_hours_used[teacher_entry['id']] >= teacher_entry['total_hours']:
                            continue
                        
                        if teacher_class_hours_used[(teacher_entry['id'], class_name)] >= teacher_entry['max_hours_this_class']:
                            continue
                        
                        available_teacher = teacher_entry
                        break
                
                if available_teacher:
                    class_timetable[class_name][target_day][target_period] = f"{subject}|{available_teacher['name']}"
                    teacher_hours_used[available_teacher['id']] += 1
                    teacher_class_hours_used[(available_teacher['id'], class_name)] += 1
    
    # ========== ステップ2: 技能教科を配置（優先度付き・少ないコマ順） ==========
    for class_name in all_classes:
        grade = class_name[0]
        grade_key = f"{grade}年"
        required_subjects = SUBJECT_MASTER.get(grade_key, {})
        
        for subject in SKILL_SUBJECTS:  # ★優先度付き
            if subject not in required_subjects:
                continue
            
            required_hours = required_subjects[subject]
            current_count = sum(1 for day in days for period in periods 
                              if class_timetable[class_name][day].get(period) and 
                              subject in str(class_timetable[class_name][day].get(period)))
            
            needed = required_hours - current_count
            if needed <= 0:
                continue
            
            for _ in range(needed):
                valid_slots = []
                for day in days:
                    for period in periods:
                        if class_timetable[class_name][day][period] is not None:
                            continue
                        
                        if day in short_days and period == 6:
                            continue
                        
                        same_subject_count_today = sum(1 for p in periods 
                                                      if class_timetable[class_name][day].get(p) and 
                                                      subject in str(class_timetable[class_name][day].get(p)))
                        if same_subject_count_today > 0:
                            continue
                        
                        prev_subject = None
                        next_subject = None
                        if period > 1:
                            prev_val = class_timetable[class_name][day].get(period - 1)
                            if prev_val and prev_val != "BLOCKED":
                                prev_subject = prev_val.split('|')[0]
                        if period < 6:
                            next_val = class_timetable[class_name][day].get(period + 1)
                            if next_val and next_val != "BLOCKED":
                                next_subject = next_val.split('|')[0]
                        
                        if prev_subject == subject or next_subject == subject:
                            continue
                        
                        if subject in facility_limits:
                            current_facility_usage = sum(1 for c in all_classes 
                                                        for p in periods 
                                                        if class_timetable[c][day].get(p) and 
                                                        subject in str(class_timetable[c][day].get(p)))
                            if current_facility_usage >= facility_limits[subject]:
                                continue
                        
                        valid_slots.append((day, period))
                
                if not valid_slots:
                    break
                
                # ★木金優先
                valid_slots = sort_slots_by_day(valid_slots)
                random.shuffle(valid_slots)
                
                placed = False
                for day, period in valid_slots:
                    available_teacher = None
                    if subject in subject_teachers:
                        for teacher_entry in subject_teachers[subject]:
                            if teacher_entry['class'] != class_name:
                                continue
                            
                            if (teacher_entry['id'], day, period) in ng_set:
                                continue
                            
                            if teacher_hours_used[teacher_entry['id']] >= teacher_entry['total_hours']:
                                continue
                            
                            if teacher_class_hours_used[(teacher_entry['id'], class_name)] >= teacher_entry['max_hours_this_class']:
                                continue
                            
                            available_teacher = teacher_entry
                            break
                    
                    if available_teacher:
                        class_timetable[class_name][day][period] = f"{subject}|{available_teacher['name']}"
                        teacher_hours_used[available_teacher['id']] += 1
                        teacher_class_hours_used[(available_teacher['id'], class_name)] += 1
                        placed = True
                        break
    
    # ========== ステップ3: 基礎5教科を配置（優先度付き・少ないコマ順） ==========
    for class_name in all_classes:
        grade = class_name[0]
        grade_key = f"{grade}年"
        required_subjects = SUBJECT_MASTER.get(grade_key, {})
        
        for subject in CORE_SUBJECTS:  # ★優先度付き
            if subject not in required_subjects:
                continue
            
            required_hours = required_subjects[subject]
            current_count = sum(1 for day in days for period in periods 
                              if class_timetable[class_name][day].get(period) and 
                              subject in str(class_timetable[class_name][day].get(period)))
            
            needed = required_hours - current_count
            if needed <= 0:
                continue
            
            for _ in range(needed):
                valid_slots = []
                for day in days:
                    for period in periods:
                        if class_timetable[class_name][day][period] is not None:
                            continue
                        
                        if day in short_days and period == 6:
                            continue
                        
                        same_subject_count_today = sum(1 for p in periods 
                                                      if class_timetable[class_name][day].get(p) and 
                                                      subject in str(class_timetable[class_name][day].get(p)))
                        if same_subject_count_today > 0:
                            continue
                        
                        prev_subject = None
                        next_subject = None
                        if period > 1:
                            prev_val = class_timetable[class_name][day].get(period - 1)
                            if prev_val and prev_val != "BLOCKED":
                                prev_subject = prev_val.split('|')[0]
                        if period < 6:
                            next_val = class_timetable[class_name][day].get(period + 1)
                            if next_val and next_val != "BLOCKED":
                                next_subject = next_val.split('|')[0]
                        
                        if prev_subject == subject or next_subject == subject:
                            continue
                        
                        valid_slots.append((day, period))
                
                if not valid_slots:
                    break
                
                # ★木金優先
                valid_slots = sort_slots_by_day(valid_slots)
                random.shuffle(valid_slots)
                
                placed = False
                for day, period in valid_slots:
                    available_teacher = None
                    if subject in subject_teachers:
                        for teacher_entry in subject_teachers[subject]:
                            if teacher_entry['class'] != class_name:
                                continue
                            
                            if (teacher_entry['id'], day, period) in ng_set:
                                continue
                            
                            if teacher_hours_used[teacher_entry['id']] >= teacher_entry['total_hours']:
                                continue
                            
                            if teacher_class_hours_used[(teacher_entry['id'], class_name)] >= teacher_entry['max_hours_this_class']:
                                continue
                            
                            available_teacher = teacher_entry
                            break
                    
                    if available_teacher:
                        class_timetable[class_name][day][period] = f"{subject}|{available_teacher['name']}"
                        teacher_hours_used[available_teacher['id']] += 1
                        teacher_class_hours_used[(available_teacher['id'], class_name)] += 1
                        placed = True
                        break
    
    # ========== 結果を配列に変換 ==========
    schedule = []
    for class_name in all_classes:
        for day in days:
            for period in periods:
                slot_value = class_timetable[class_name][day][period]
                if slot_value and slot_value != "BLOCKED":
                    subject, teacher_name = slot_value.split('|')
                    schedule.append({
                        "class": class_name,
                        "day": day,
                        "period": period,
                        "subject": subject,
                        "teacher_name": teacher_name
                    })
    
    return schedule

def calculate_fill_rate(schedule, all_classes, days, short_days):
    """充填率を計算（0.0-1.0）"""
    total_possible = 0
    filled = len(schedule)
    
    for _ in all_classes:
        for day in days:
            if day in short_days:
                total_possible += 5  # 月火水木金 の1-5限
            else:
                total_possible += 6  # 1-6限
    
    if total_possible == 0:
        return 0.0
    return filled / total_possible

@app.post("/optimize")
def optimize_schedule(request: ScheduleRequest):
    """
    時間割最適化エンドポイント（v2.2.15・複数試行版）
    
    5回試行して、最も充填率が高い結果を返す
    """
    
    try:
        # ========== データの正規化 ==========
        normalized_teachers = []
        for teacher in request.teachers:
            try:
                teacher_id = int(teacher.id) if isinstance(teacher.id, (int, float, str)) else None
                if not teacher_id or teacher_id <= 0:
                    print(f"DEBUG: 無効な教員ID: {teacher.id}, スキップ")
                    continue
                
                classes = teacher.classes if isinstance(teacher.classes, list) else []
                if isinstance(teacher.classes, str):
                    classes = [c.strip() for c in teacher.classes.split(',') if c.strip()]
                
                if not classes:
                    continue
                
                hours = int(teacher.hours) if isinstance(teacher.hours, (int, float, str)) else 0
                if hours <= 0:
                    continue
                
                normalized_teachers.append({
                    'id': teacher_id,
                    'name': teacher.name,
                    'subject': teacher.subject,
                    'classes': classes,
                    'hours': hours
                })
            except Exception as e:
                print(f"DEBUG: 教員データの正規化エラー: {e}, スキップ")
                continue
        
        if not normalized_teachers:
            return {
                "schedule": [],
                "status": "ERROR",
                "message": "有効な教員データがありません"
            }
        
        days = ["月", "火", "水", "木", "金"]
        short_days = request.short_days or ["水"]
        
        # 全クラスを取得
        all_classes = set()
        for teacher in normalized_teachers:
            all_classes.update(teacher['classes'])
        all_classes = sorted(list(all_classes))
        
        # NG時間帯をセット化
        ng_set = set()
        for ng in request.ng_list or []:
            if ng.target_type == "teacher":
                ng_set.add((int(ng.target_id), ng.day, ng.period))
        
        # 施設上限を設定
        facility_limits = request.facility_limits or {}
        
        # 学年一斉コマをセット化
        group_slots_by_time = {}
        for gs in request.group_slots or []:
            key = (gs.grade, gs.day, gs.period)
            group_slots_by_time[key] = gs.subject
        
        # ========== 複数回試行 ==========
        print("DEBUG: ========== 複数回試行を開始 ==========")
        
        best_schedule = []
        best_fill_rate = 0.0
        
        for trial in range(5):
            print(f"DEBUG: 試行 {trial + 1}/5")
            
            schedule = optimize_schedule_single(
                normalized_teachers,
                ng_set,
                facility_limits,
                group_slots_by_time,
                short_days
            )
            
            fill_rate = calculate_fill_rate(schedule, all_classes, days, short_days)
            print(f"DEBUG: 試行 {trial + 1} 充填率={fill_rate:.1%} ({len(schedule)}コマ)")
            
            if fill_rate > best_fill_rate:
                best_fill_rate = fill_rate
                best_schedule = schedule
        
        print(f"DEBUG: 最良試行の充填率={best_fill_rate:.1%} ({len(best_schedule)}コマ)")
        
        return {
            "schedule": best_schedule,
            "status": "SUCCESS",
            "message": f"時間割生成完了: {len(best_schedule)}コマ配置（充填率: {best_fill_rate:.1%}）",
            "fill_rate": best_fill_rate
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
