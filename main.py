from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional, Dict
import random

app = FastAPI(title="学校時間割最適化 API", version="2.2.13")

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
    classes: List[str]  # リスト形式
    hours: int
    
    class Config:
        # 文字列をリストに変換
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
    return {"message": "学校時間割最適化API v2.2.13（制度設計版）"}

@app.post("/optimize")
def optimize_schedule(request: ScheduleRequest):
    """
    時間割最適化エンドポイント（v2.2.13・制度設計版）
    
    制度設計：
    - 教員が複数クラスを担当する場合、各クラスで個別に配置
    - 例：教員1（国語・1-1,1-2,1-3）→ 3個の スケジュール対象
    
    優先順位：
    1. 特別活動（道徳・学活・総合）→ 学年一斉で固定
    2. 技能教科（保体・理科・音楽・美術・技術・家庭）→ 特別教室が限られている
    3. 基礎5教科（国語・社会・数学・英語）→ 普通教室で自由に配置
    
    制約：
    - 同一教科の連続配置を禁止
    - 教員の週コマ数制限
    - NG時間帯
    - 施設上限
    - 学年一斉コマ
    - 1日1クラス1教科1コマ（特別活動を除く）
    """
    
    try:
        # ========== データの正規化 ==========
        # 教員データを再度バリデーション・正規化
        normalized_teachers = []
        for teacher in request.teachers:
            try:
                # IDが有効な整数か確認
                teacher_id = int(teacher.id) if isinstance(teacher.id, (int, float, str)) else None
                if not teacher_id or teacher_id <= 0:
                    print(f"DEBUG: 無効な教員ID: {teacher.id}, スキップ")
                    continue
                
                # クラスが空でないか確認
                classes = teacher.classes if isinstance(teacher.classes, list) else []
                if isinstance(teacher.classes, str):
                    classes = [c.strip() for c in teacher.classes.split(',') if c.strip()]
                
                if not classes:
                    print(f"DEBUG: 教員{teacher.name}の担当クラスが空, スキップ")
                    continue
                
                # 週コマ数が有効か確認
                hours = int(teacher.hours) if isinstance(teacher.hours, (int, float, str)) else 0
                if hours <= 0:
                    print(f"DEBUG: 教員{teacher.name}の週コマ数が0以下: {hours}, スキップ")
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
        
        print(f"DEBUG: 正規化後の教員数: {len(normalized_teachers)}")
        for t in normalized_teachers:
            print(f"DEBUG: 教員 {t['name']} → 科目: {t['subject']}, クラス: {t['classes']}, 週コマ: {t['hours']}")
        days = ["月", "火", "水", "木", "金"]
        periods = [1, 2, 3, 4, 5, 6]
        short_days = request.short_days or ["水"]
        
        # 全クラスを取得（正規化済みデータから）
        all_classes = set()
        for teacher in normalized_teachers:
            all_classes.update(teacher['classes'])
        all_classes = sorted(list(all_classes))
        
        print(f"DEBUG: classes={all_classes}")
        print(f"DEBUG: num_classes={len(all_classes)}")
        
        # 教科別に教員を分類（各クラスごと）
        subject_teachers = {}
        for teacher in normalized_teachers:
            if teacher['subject'] not in subject_teachers:
                subject_teachers[teacher['subject']] = []
            
            # 教員が複数クラスを担当する場合、各クラスごとに分割
            # ★重要★ 各クラスでの上限 = 総週コマ数 ÷ 担当クラス数
            hours_per_class = teacher['hours'] // len(teacher['classes']) if teacher['classes'] else teacher['hours']
            
            for class_name in teacher['classes']:
                subject_teachers[teacher['subject']].append({
                    'id': teacher['id'],
                    'name': teacher['name'],
                    'subject': teacher['subject'],
                    'class': class_name,
                    'original_classes': teacher['classes'],
                    'total_hours': teacher['hours'],  # 総時間数（表示・追跡用）
                    'max_hours_this_class': hours_per_class  # このクラスでの上限 ★重要★
                })
        
        print(f"DEBUG: subjects={list(subject_teachers.keys())}")
        
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
        teacher_hours_used = {}  # teacher_id → 総時間数
        teacher_class_hours_used = {}  # (teacher_id, class_name) → このクラスでの時間数
        for teacher in normalized_teachers:
            teacher_hours_used[teacher['id']] = 0
            for class_name in teacher['classes']:
                teacher_class_hours_used[(teacher['id'], class_name)] = 0
        
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
        print("DEBUG: ========== ステップ1: 特別活動を配置 ==========")
        
        for class_name in all_classes:
            grade = class_name[0]
            grade_key = f"{grade}年"
            required_subjects = SUBJECT_MASTER.get(grade_key, {})
            
            for subject in SPECIAL_ACTIVITIES:
                if subject not in required_subjects:
                    continue
                
                placed_count = 0
                
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
                            
                            # ★制約1: 総時間数の制限
                            if teacher_hours_used[teacher_entry['id']] >= teacher_entry['total_hours']:
                                continue
                            
                            # ★制約2: このクラスでの上限
                            if teacher_class_hours_used[(teacher_entry['id'], class_name)] >= teacher_entry['max_hours_this_class']:
                                continue
                            
                            available_teacher = teacher_entry
                            break
                    
                    if available_teacher:
                        class_timetable[class_name][target_day][target_period] = f"{subject}|{available_teacher['name']}"
                        teacher_hours_used[available_teacher['id']] += 1
                        teacher_class_hours_used[(available_teacher['id'], class_name)] += 1
                        placed_count += 1
                
                print(f"DEBUG: {class_name} {subject}={placed_count}（学年一斉コマのみ）")
        
        # ========== ステップ2: 技能教科を配置 ==========
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
                    # スロットを先に絞る
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
                        print(f"DEBUG: {class_name} {subject}: 有効なスロットなし")
                        break
                    
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
                                
                                # ★制約1: 総時間数の制限
                                if teacher_hours_used[teacher_entry['id']] >= teacher_entry['total_hours']:
                                    continue
                                
                                # ★制約2: このクラスでの上限
                                if teacher_class_hours_used[(teacher_entry['id'], class_name)] >= teacher_entry['max_hours_this_class']:
                                    continue
                                
                                available_teacher = teacher_entry
                                break
                        
                        if available_teacher:
                            class_timetable[class_name][day][period] = f"{subject}|{available_teacher['name']}"
                            teacher_hours_used[available_teacher['id']] += 1
                            teacher_class_hours_used[(available_teacher['id'], class_name)] += 1
                            placed = True
                            placed_count += 1
                            break
                    
                    if not placed:
                        print(f"DEBUG: {class_name} {subject}: 利用可能な教員がない")
                        break
                
                print(f"DEBUG: {class_name} {subject}={placed_count}/{needed}")
        
        # ========== ステップ3: 基礎5教科を配置 ==========
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
                        print(f"DEBUG: {class_name} {subject}: 有効なスロットなし")
                        break
                    
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
                                
                                # ★制約1: 総時間数の制限
                                if teacher_hours_used[teacher_entry['id']] >= teacher_entry['total_hours']:
                                    continue
                                
                                # ★制約2: このクラスでの上限
                                if teacher_class_hours_used[(teacher_entry['id'], class_name)] >= teacher_entry['max_hours_this_class']:
                                    continue
                                
                                available_teacher = teacher_entry
                                break
                        
                        if available_teacher:
                            class_timetable[class_name][day][period] = f"{subject}|{available_teacher['name']}"
                            teacher_hours_used[available_teacher['id']] += 1
                            teacher_class_hours_used[(available_teacher['id'], class_name)] += 1
                            placed = True
                            placed_count += 1
                            break
                    
                    if not placed:
                        print(f"DEBUG: {class_name} {subject}: 利用可能な教員がない")
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
        
        # 教員ごとの配置コマ数をサマリー
        teacher_summary = {}
        for item in schedule:
            teacher_name = item.get('teacher_name', 'unknown')
            teacher_summary[teacher_name] = teacher_summary.get(teacher_name, 0) + 1
        
        print(f"DEBUG: 教員別配置コマ数: {teacher_summary}")
        
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
