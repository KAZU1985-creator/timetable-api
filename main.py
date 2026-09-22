from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional, Dict
import random

app = FastAPI(title="学校時間割最適化 API", version="2.2.12")

# ========== 教科マスタ（固定） ==========
SUBJECT_MASTER = {
    '1年': {'国語': 4, '社会': 4, '数学': 4, '理科': 3, '英語': 3, '音楽': 1, '美術': 1, '保体': 1, '技術': 1, '家庭': 1, '学活': 1, '総合': 1, '道徳': 1},
    '2年': {'国語': 4, '社会': 4, '数学': 4, '理科': 3, '英語': 3, '音楽': 1, '美術': 1, '保体': 1, '技術': 1, '家庭': 1, '学活': 1, '総合': 1, '道徳': 1},
    '3年': {'国語': 4, '社会': 4, '数学': 4, '理科': 3, '英語': 3, '音楽': 1, '美術': 1, '保体': 1, '技術': 1, '家庭': 1, '学活': 1, '総合': 1, '道徳': 1},
}

# ========== 特別教室が必要な教科 ==========
FACILITY_SUBJECTS = {
    '保体': '体育館',
    '理科': '理科室',
    '音楽': '音楽室',
    '美術': '美術室',
    '技術': '技術室'
}

# ========== 特別活動（道徳・学活・総合） ==========
SPECIAL_ACTIVITIES = ['道徳', '学活', '総合']

# ========== 技能教科（特別教室が必要）==========
SKILL_SUBJECTS = ['保体', '理科', '音楽', '美術', '技術', '家庭']

# ========== 基礎5教科 ==========
CORE_SUBJECTS = ['国語', '社会', '数学', '英語']

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

@app.get("/")
def read_root():
    return {"message": "学校時間割最適化API v2.2.12（サンプルデータに総合を含む）"}

@app.post("/optimize")
def optimize_schedule(request: ScheduleRequest):
    """
    時間割最適化エンドポイント（v2.2.12）
    
    正しい優先順位：
    1. 特別活動（道徳・学活・総合）→ 学年一斉で固定
    2. 技能教科（保体・理科・音楽・美術・技術・家庭）→ 特別教室が限られている
    3. 基礎5教科（国語・社会・数学・英語）→ 普通教室で自由に配置
    
    制約：
    - 同一教科の連続配置を禁止
    - 教員の週コマ数制限
    - NG時間帯
    - 施設上限
    - 学年一斉コマ
    
    サンプルグループスロット（GASから送信）：
    - 1年・木・1・道徳
    - 2年・金・1・道徳
    - 3年・月・1・道徳
    - 1年・月・6・学活
    - 2年・火・6・学活
    - 3年・水・5・学活
    - 1年・金・6・総合 ← 新規追加
    - 2年・月・6・総合 ← 新規追加
    - 3年・木・6・総合 ← 新規追加
    """
    
    try:
        days = ["月", "火", "水", "木", "金"]
        periods = [1, 2, 3, 4, 5, 6]
        short_days = request.short_days or ["水"]
        
        # 全クラスを取得
        all_classes = set()
        for teacher in request.teachers:
            all_classes.update(teacher.classes)
        all_classes = sorted(list(all_classes))
        
        print(f"DEBUG: classes={all_classes}")
        
        # 教科別に教員を分類
        subject_teachers = {}
        for teacher in request.teachers:
            if teacher.subject not in subject_teachers:
                subject_teachers[teacher.subject] = []
            subject_teachers[teacher.subject].append(teacher)
        
        print(f"DEBUG: subjects={list(subject_teachers.keys())}")
        print(f"DEBUG: group_slots={request.group_slots}")
        
        # 各クラスの時間割グリッドを初期化
        class_timetable = {}
        for class_name in all_classes:
            class_timetable[class_name] = {}
            for day in days:
                class_timetable[class_name][day] = {}
                for period in periods:
                    if day in short_days and period == 6:
                        class_timetable[class_name][day][period] = "BLOCKED"  # 水曜の6限は配置不可
                    else:
                        class_timetable[class_name][day][period] = None
        
        # 教員の使用時間数を追跡
        teacher_hours_used = {}
        for teacher in request.teachers:
            teacher_hours_used[teacher.id] = 0
        
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
        
        # ========== ステップ1: 特別活動を配置（学年一斉コマのみ） ==========
        print("DEBUG: ========== ステップ1: 特別活動を配置（学年一斉コマのみ） ==========")
        
        for class_name in all_classes:
            grade = class_name[0]  # "1-1" -> "1"
            grade_key = f"{grade}年"
            required_subjects = SUBJECT_MASTER.get(grade_key, {})
            
            for subject in SPECIAL_ACTIVITIES:
                if subject not in required_subjects:
                    continue
                
                placed_count = 0
                
                # 学年一斉コマに設定されたものだけを配置
                for (target_grade, target_day, target_period), target_subject in group_slots_by_time.items():
                    if target_grade != grade or target_subject != subject:
                        continue
                    
                    if class_timetable[class_name][target_day][target_period] is not None:
                        continue
                    
                    # 利用可能な教員を探す（担当教員を優先）
                    available_teacher = None
                    if subject in subject_teachers:
                        for teacher in subject_teachers[subject]:
                            if class_name not in teacher.classes:
                                continue
                            
                            if (teacher.id, target_day, target_period) in ng_set:
                                continue
                            
                            if teacher_hours_used[teacher.id] >= teacher.hours:
                                continue
                            
                            available_teacher = teacher
                            break
                    
                    if available_teacher:
                        class_timetable[class_name][target_day][target_period] = f"{subject}|{available_teacher.name}"
                        teacher_hours_used[available_teacher.id] += 1
                        placed_count += 1
                
                print(f"DEBUG: {class_name} {subject}={placed_count}（学年一斉コマのみ）")
        
        # ========== ステップ2: 技能教科を配置（特別教室が被らないように・1日1コマ制限） ==========
        print("DEBUG: ========== ステップ2: 技能教科を配置 ==========")
        
        for class_name in all_classes:
            grade = class_name[0]
            grade_key = f"{grade}年"
            required_subjects = SUBJECT_MASTER.get(grade_key, {})
            
            for subject in SKILL_SUBJECTS:
                if subject not in required_subjects:
                    continue
                
                required_hours = required_subjects[subject]
                current_count = sum(1 for day in days for period in periods 
                                  if class_timetable[class_name][day].get(period) and 
                                  subject in str(class_timetable[class_name][day].get(period)))
                
                needed = required_hours - current_count
                if needed <= 0:
                    continue
                
                placed_count = 0
                
                for _ in range(needed):
                    placed = False
                    
                    slot_list = []
                    for day in days:
                        for period in periods:
                            if class_timetable[class_name][day][period] is None:
                                slot_list.append((day, period))
                    
                    random.shuffle(slot_list)
                    
                    for day, period in slot_list:
                        if day in short_days and period == 6:
                            continue
                        
                        # ========== 制約1: 同じ日に同じ技能教科が複数配置されないようにチェック ==========
                        same_subject_count_today = sum(1 for p in periods 
                                                      if class_timetable[class_name][day].get(p) and 
                                                      subject in str(class_timetable[class_name][day].get(p)))
                        if same_subject_count_today > 0:
                            continue  # この日はこの教科が既にあるのでスキップ
                        
                        # ========== 制約2: 連続チェック ==========
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
                        
                        # 施設上限をチェック
                        if subject in facility_limits:
                            current_facility_usage = sum(1 for c in all_classes 
                                                        for p in periods 
                                                        if class_timetable[c][day].get(p) and 
                                                        subject in str(class_timetable[c][day].get(p)))
                            if current_facility_usage >= facility_limits[subject]:
                                continue
                        
                        # 利用可能な教員を探す
                        available_teacher = None
                        if subject in subject_teachers:
                            for teacher in subject_teachers[subject]:
                                if class_name not in teacher.classes:
                                    continue
                                
                                if (teacher.id, day, period) in ng_set:
                                    continue
                                
                                if teacher_hours_used[teacher.id] >= teacher.hours:
                                    continue
                                
                                available_teacher = teacher
                                break
                        
                        if available_teacher:
                            class_timetable[class_name][day][period] = f"{subject}|{available_teacher.name}"
                            teacher_hours_used[available_teacher.id] += 1
                            placed = True
                            placed_count += 1
                            break
                
                print(f"DEBUG: {class_name} {subject}={placed_count}/{needed}")
        
        # ========== ステップ3: 基礎5教科を配置（1日1クラス1教科1コマ制限） ==========
        print("DEBUG: ========== ステップ3: 基礎5教科を配置 ==========")
        
        for class_name in all_classes:
            grade = class_name[0]
            grade_key = f"{grade}年"
            required_subjects = SUBJECT_MASTER.get(grade_key, {})
            
            for subject in CORE_SUBJECTS:
                if subject not in required_subjects:
                    continue
                
                required_hours = required_subjects[subject]
                current_count = sum(1 for day in days for period in periods 
                                  if class_timetable[class_name][day].get(period) and 
                                  subject in str(class_timetable[class_name][day].get(period)))
                
                needed = required_hours - current_count
                if needed <= 0:
                    continue
                
                placed_count = 0
                
                for _ in range(needed):
                    placed = False
                    
                    slot_list = []
                    for day in days:
                        for period in periods:
                            if class_timetable[class_name][day][period] is None:
                                slot_list.append((day, period))
                    
                    random.shuffle(slot_list)
                    
                    for day, period in slot_list:
                        if day in short_days and period == 6:
                            continue
                        
                        # ========== 制約1: 同じ日に同じ5教科が複数配置されないようにチェック ==========
                        same_subject_count_today = sum(1 for p in periods 
                                                      if class_timetable[class_name][day].get(p) and 
                                                      subject in str(class_timetable[class_name][day].get(p)))
                        if same_subject_count_today > 0:
                            continue  # この日はこの教科が既にあるのでスキップ
                        
                        # ========== 制約2: 連続チェック ==========
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
                        
                        # 利用可能な教員を探す
                        available_teacher = None
                        if subject in subject_teachers:
                            for teacher in subject_teachers[subject]:
                                if class_name not in teacher.classes:
                                    continue
                                
                                if (teacher.id, day, period) in ng_set:
                                    continue
                                
                                if teacher_hours_used[teacher.id] >= teacher.hours:
                                    continue
                                
                                available_teacher = teacher
                                break
                        
                        if available_teacher:
                            class_timetable[class_name][day][period] = f"{subject}|{available_teacher.name}"
                            teacher_hours_used[available_teacher.id] += 1
                            placed = True
                            placed_count += 1
                            break
                
                print(f"DEBUG: {class_name} {subject}={placed_count}/{needed}")
        
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
        
        # クラスごとの教科統計を出力
        for class_name in all_classes:
            subject_counts = {}
            for item in schedule:
                if item["class"] == class_name:
                    subj = item["subject"]
                    subject_counts[subj] = subject_counts.get(subj, 0) + 1
            print(f"DEBUG: {class_name}={subject_counts}")
        
        print(f"DEBUG: total_schedule={len(schedule)}")
        
        return {
            "schedule": schedule,
            "status": "SUCCESS",
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
