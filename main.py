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

def try_place_subject(class_timetable, class_name, subject, 
                      subject_teachers, teacher_hours_used, teacher_class_hours_used,
                      ng_set, facility_limits, short_days, all_classes=None):
    """
    1コマ分の教科を配置する（曜日優先度で試す）
    戻り値: 配置成功 (True/False)
    """
    days = ["月", "火", "水", "木", "金"]
    periods = [1, 2, 3, 4, 5, 6]
    
    if all_classes is None:
        all_classes = list(class_timetable.keys())
    
    # 有効なスロットを収集（曜日別）
    valid_slots_by_day = {day: [] for day in days}
    
    for day in days:
        for period in periods:
            if class_timetable[class_name][day][period] is not None:
                continue
            
            if day in short_days and period == 6:
                continue
            
            # 同一曜日に同一教科が既にあるか
            same_subject_count_today = sum(1 for p in periods 
                                          if class_timetable[class_name][day].get(p) and 
                                          subject in str(class_timetable[class_name][day].get(p)))
            if same_subject_count_today > 0:
                continue
            
            # 連続配置チェック（より厳密に）
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
            
            # ★同じ教科が連続で配置されていないかチェック
            if prev_subject == subject or next_subject == subject:
                continue
            
            # ★同じ教員の複数クラスが同じ限に配置されていないかチェック
            same_teacher_same_period = False
            if subject in subject_teachers:
                for teacher_entry in subject_teachers[subject]:
                    # この教員が複数クラスを担当しているか
                    teacher_classes = teacher_entry.get('original_classes', [])
                    if len(teacher_classes) > 1:
                        # この教員の別のクラスが同じ限に同じ教科を配置していないかチェック
                        for other_class in teacher_classes:
                            if other_class == class_name:
                                continue
                            other_slot = class_timetable[other_class][day].get(period)
                            if other_slot and other_slot != "BLOCKED" and subject in str(other_slot):
                                same_teacher_same_period = True
                                break
                    if same_teacher_same_period:
                        break
            
            if same_teacher_same_period:
                continue
            
            # 施設上限チェック（★「曜日+限」単位で制限）
            if subject in facility_limits:
                current_facility_usage = sum(1 for c in all_classes 
                                            if class_timetable[c][day].get(period) and 
                                            subject in str(class_timetable[c][day].get(period)))
                if current_facility_usage >= facility_limits[subject]:
                    continue
            
            valid_slots_by_day[day].append(period)
    
    # ★曜日バランス重視：全曜日を同等に扱う（ランダム化）
    # （金→木優先を廃止し、月火水木金に均等に分散させる）
    all_valid_slots = []
    for day in days:
        for period in valid_slots_by_day[day]:
            all_valid_slots.append((day, period))
    
    if not all_valid_slots:
        return False
    
    # ★ランダムに選ぶ（曜日優先度なし）
    random.shuffle(all_valid_slots)
    
    for day, period in all_valid_slots:
        # ★配置前に連続配置をチェック
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
            continue  # この(day, period)はスキップ
        
        # ★配置前に施設上限を再度チェック（念のため）
        if subject in facility_limits:
            current_facility_usage = sum(1 for c in all_classes 
                                        if class_timetable[c][day].get(period) and 
                                        subject in str(class_timetable[c][day].get(period)))
            if current_facility_usage >= facility_limits[subject]:
                continue  # 施設上限に達していればスキップ
        
        # 利用可能な教員を探す
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
            return True
    
    return False

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
                # ★新しい関数で配置を試みる（曜日優先度を守る）
                placed = try_place_subject(
                    class_timetable, class_name, subject,
                    subject_teachers, teacher_hours_used, teacher_class_hours_used,
                    ng_set, facility_limits, short_days, all_classes
                )
                if not placed:
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
                # ★新しい関数で配置を試みる（曜日優先度を守る）
                placed = try_place_subject(
                    class_timetable, class_name, subject,
                    subject_teachers, teacher_hours_used, teacher_class_hours_used,
                    ng_set, facility_limits, short_days, all_classes
                )
                if not placed:
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

def count_violations(schedule, facility_limits, all_classes):
    """
    制約違反の数をカウント（より厳密に）
    - 同じ限に同じ教科が複数クラス配置されている（横かぶり）
    - 同じクラスで同じ教科が連続している（縦かぶり）
    - 同じ教員が同じ限に複数クラスを教えている
    """
    days = ["月", "火", "水", "木", "金"]
    periods = [1, 2, 3, 4, 5, 6]
    
    violation_count = 0
    
    # 違反1：施設上限違反をカウント（横かぶり）
    for day in days:
        for period in periods:
            for subject, limit in facility_limits.items():
                count = sum(1 for item in schedule 
                           if item['day'] == day and item['period'] == period and item['subject'] == subject)
                if count > limit:
                    # ★超過分を全てカウント
                    violation_count += (count - limit)
    
    # 違反2：連続配置違反をカウント（縦かぶり）
    for class_name in all_classes:
        for day in days:
            for period in range(1, 6):  # 1-5限（6限の次はない）
                item1 = next((x for x in schedule if x['class'] == class_name and x['day'] == day and x['period'] == period), None)
                item2 = next((x for x in schedule if x['class'] == class_name and x['day'] == day and x['period'] == period + 1), None)
                
                if item1 and item2 and item1['subject'] == item2['subject']:
                    violation_count += 1
    
    # 違反3：同じ教員が複数クラスで同じ限に配置
    for day in days:
        for period in periods:
            teacher_subjects = {}  # 教員 -> (教科, クラス) のマッピング
            
            for item in schedule:
                if item['day'] != day or item['period'] != period:
                    continue
                
                # teacher_name から教員を特定（簡易版）
                teacher_name = item.get('teacher_name', '')
                subject = item['subject']
                class_name = item['class']
                
                if teacher_name not in teacher_subjects:
                    teacher_subjects[teacher_name] = []
                
                teacher_subjects[teacher_name].append((subject, class_name))
            
            # 同じ教員が同じ限に複数クラスを教えていないか確認
            for teacher_name, subject_classes in teacher_subjects.items():
                for subject, class_name in subject_classes:
                    # この教員が同じ限に同じ教科を別のクラスで教えているか
                    for other_subject, other_class in subject_classes:
                        if class_name != other_class and subject == other_subject:
                            violation_count += 1
                            break  # 1クラスの重複は1回だけカウント
    
    return violation_count

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

def fill_gaps_safely(schedule, all_classes, days, short_days):
    """
    安全な空白埋め（必須コマ数を超えない）
    - 不足している教科のみを埋める
    - コマ数上限を絶対に超えない
    - 連続配置や同一曜日の制約も守る
    """
    # 各クラスの時間割を辞書化
    class_timetable = {}
    for class_name in all_classes:
        class_timetable[class_name] = {}
        for day in days:
            class_timetable[class_name][day] = {}
            for period in range(1, 7):
                class_timetable[class_name][day][period] = None
    
    # スケジュールをグリッドに戻す
    for item in schedule:
        class_timetable[item['class']][item['day']][item['period']] = item
    
    # 各クラスごとに不足を埋める
    new_schedule = list(schedule)
    
    for class_name in all_classes:
        grade = class_name[0]
        grade_key = f"{grade}年"
        required_subjects = SUBJECT_MASTER.get(grade_key, {})
        
        # 現在のコマ数を集計
        current_counts = {}
        for item in schedule:
            if item['class'] == class_name:
                current_counts[item['subject']] = current_counts.get(item['subject'], 0) + 1
        
        # 不足している教科を探す
        deficit_subjects = []
        for subject, required in required_subjects.items():
            current = current_counts.get(subject, 0)
            deficit = required - current
            
            # ★重要：不足分のみ（負数=超過はスキップ）
            if deficit > 0:
                for _ in range(deficit):
                    deficit_subjects.append(subject)
        
        if not deficit_subjects:
            continue  # このクラスに不足なし
        
        # 空きスロットを収集
        empty_slots = []
        for day in days:
            for period in range(1, 7):
                if day in short_days and period == 6:
                    continue
                if class_timetable[class_name][day][period] is None:
                    empty_slots.append((day, period))
        
        if not empty_slots:
            continue  # 空きスロットなし
        
        # ランダムに埋める（必要な分だけ）
        random.shuffle(empty_slots)
        random.shuffle(deficit_subjects)
        
        slot_idx = 0
        for subject in deficit_subjects:
            if slot_idx >= len(empty_slots):
                break  # スロット不足
            
            day, period = empty_slots[slot_idx]
            
            # ★安全チェック1：同一曜日に同じ教科がないか
            same_subject_count = sum(1 for p in range(1, 7)
                                    if class_timetable[class_name][day][p] 
                                    and class_timetable[class_name][day][p]['subject'] == subject)
            if same_subject_count > 0:
                continue  # 同一曜日に同じ教科 → この slot を飛ばす
            
            # ★安全チェック2：連続配置がないか
            prev_period = period - 1
            next_period = period + 1
            prev_subject = None
            next_subject = None
            
            if prev_period >= 1:
                prev_item = class_timetable[class_name][day][prev_period]
                if prev_item:
                    prev_subject = prev_item['subject']
            
            if next_period <= 6:
                next_item = class_timetable[class_name][day][next_period]
                if next_item:
                    next_subject = next_item['subject']
            
            if prev_subject == subject or next_subject == subject:
                continue  # 連続配置 → この slot を飛ばす
            
            # ★埋める
            new_schedule.append({
                "class": class_name,
                "day": day,
                "period": period,
                "subject": subject,
                "teacher_name": "（補填）"
            })
            class_timetable[class_name][day][period] = {
                "subject": subject
            }
            slot_idx += 1
    
    return new_schedule

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
        
        # ★基礎5教科にもデフォルト施設上限を設定（GASから受け取らない場合の保険）
        core_subjects_with_defaults = ['国語', '社会', '数学', '英語']
        for subj in core_subjects_with_defaults:
            if subj not in facility_limits:
                facility_limits[subj] = 1  # 同じ限に1クラスまで
        
        # ★デバッグ用ログ
        print(f"DEBUG: Received facility_limits = {request.facility_limits}")
        print(f"DEBUG: Final facility_limits = {facility_limits}")
        
        # 学年一斉コマをセット化
        group_slots_by_time = {}
        for gs in request.group_slots or []:
            key = (gs.grade, gs.day, gs.period)
            group_slots_by_time[key] = gs.subject
        
        # ========== 複数回試行 ==========
        print("DEBUG: ========== 複数回試行を開始 ==========")
        
        best_schedule = []
        best_fill_rate = 0.0
        best_violations = float('inf')  # ★違反数の初期値は無限大
        
        # ★試行回数を20回に増やす（違反0を見つける確率を上げる）
        for trial in range(20):
            print(f"DEBUG: 試行 {trial + 1}/20")
            
            schedule = optimize_schedule_single(
                normalized_teachers,
                ng_set,
                facility_limits,
                group_slots_by_time,
                short_days
            )
            
            fill_rate = calculate_fill_rate(schedule, all_classes, days, short_days)
            
            # ★制約違反をカウント
            violation_count = count_violations(schedule, facility_limits, all_classes)
            
            print(f"DEBUG: 試行 {trial + 1} 充填率={fill_rate:.1%} 違反数={violation_count}")
            
            # ★規則第一：違反0を最優先
            if violation_count == 0:
                # 違反0の試行が見つかった
                if best_violations == float('inf') or fill_rate > best_fill_rate:
                    best_fill_rate = fill_rate
                    best_violations = 0
                    best_schedule = schedule
                
                # ★違反0を見つけたら、さらに2回試行して改善できるか確認
                # （充填率をもう少し上げられるか試す）
                if trial >= 2:  # 最低3回は試行する
                    print(f"DEBUG: 違反0を発見。充填率={fill_rate:.1%}。さらに改善を試みます...")
            else:
                # 違反0がまだ見つかっていない場合のみ、違反が少ない方を更新
                if best_violations == float('inf') or violation_count < best_violations:
                    best_fill_rate = fill_rate
                    best_violations = violation_count
                    best_schedule = schedule
                elif violation_count == best_violations and fill_rate > best_fill_rate:
                    # 違反数が同じなら充填率で比較
                    best_fill_rate = fill_rate
                    best_schedule = schedule
        
        print(f"DEBUG: 最良試行の充填率={best_fill_rate:.1%} 違反数={best_violations} ({len(best_schedule)}コマ)")
        
        # ========== 空白埋め処理（安全版・必須コマ数を超えない） ==========
        print("DEBUG: ========== 空白埋めを開始 ==========")
        best_schedule = fill_gaps_safely(best_schedule, all_classes, days, short_days)
        
        final_fill_rate = calculate_fill_rate(best_schedule, all_classes, days, short_days)
        print(f"DEBUG: 空白埋め後の充填率={final_fill_rate:.1%} ({len(best_schedule)}コマ)")
        
        return {
            "schedule": best_schedule,
            "status": "SUCCESS",
            "message": f"時間割生成完了: {len(best_schedule)}コマ配置（充填率: {final_fill_rate:.1%}）",
            "fill_rate": final_fill_rate
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
