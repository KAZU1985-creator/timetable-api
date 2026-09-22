"""
学校時間割最適化 API v2.3.1

v2.3.1 の変更
  ・先生を「教員ID」ではなく「教員名」で識別（2教科を持つ先生＝教員設定で2行でも同一人物として扱う）
    旧: 行ごとに別人扱い → 同じ時間に2か所へ配置、NGも片方の行にしか効かない不具合があった


v2.3.0 の変更
  ・fixed_lessons（固定コマ）に対応：手で決めた授業を動かさず、空いているコマだけを自動で埋める
    （GAS の「空欄だけ自動で埋める」で使用。省略すれば従来どおり全体を生成）

旧版（v2.2.16）の変更

v2.2.15 からの主な変更
  1. 教員被りチェックを修正
     旧: 「その教科を持つ全学年の先生」の他クラスを見ていた
         → 1年国語と2年国語（別の先生）も同時に置けず、実質「全校で1クラスまで」になっていた
     新: 先生ごとの「空き/使用中」表で判定（特別活動も含めて、同じ先生は同じ限に1か所だけ）
  2. 学年一斉コマ（道徳・学活・総合）を最初に確保
     旧: Python側で空き扱い → 国語などが置かれ、GASで上書きされて消えていた
  3. 基礎教科（国語・社会・数学・英語）のデフォルト施設上限1を撤廃
     施設上限は GAS の設定シートに書いた教科だけに適用
  4. 「（補填）」配置を廃止
     先生なしで置くと教員被りを作るため。代わりに、置けない授業は
     既存の授業をずらして空きを作る「押し出し修復」で入れる
  5. 教科マスタ・5時間授業の曜日・特別活動の担当を GAS から受け取れるように
  6. 置けなかった授業を unplaced として返す（理由付き）
"""
from fastapi import FastAPI
from pydantic import BaseModel
from typing import List, Optional, Dict, Tuple
import random
import time

VERSION = "2.3.1"
app = FastAPI(title="学校時間割最適化 API", version=VERSION)

DAYS = ["月", "火", "水", "木", "金"]
PERIODS = [1, 2, 3, 4, 5, 6]
SPECIAL_ACTIVITIES = ["道徳", "学活", "総合"]

# GAS から教科マスタが送られてこない場合の既定値
DEFAULT_SUBJECT_MASTER = {
    g: {"国語": 4, "社会": 4, "数学": 4, "理科": 3, "英語": 3,
        "音楽": 1, "美術": 1, "保体": 1, "技術": 1, "家庭": 1,
        "学活": 1, "総合": 1, "道徳": 1}
    for g in ["1年", "2年", "3年"]
}


# ========== リクエスト定義（v2.2.15 と互換。新項目はすべて省略可） ==========
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


class SpecialTeacher(BaseModel):
    subject: str
    class_name: str
    teacher_name: str
    teacher_id: Optional[int] = None


class FixedLesson(BaseModel):
    class_name: str
    day: str
    period: int
    subject: str


class ScheduleRequest(BaseModel):
    teachers: List[TeacherAssignment]
    ng_list: Optional[List[NGItem]] = []
    group_slots: Optional[List[GroupSlot]] = []
    short_days: Optional[List[str]] = None
    special_teachers: Optional[List[SpecialTeacher]] = []
    subject_hours: Optional[Dict[str, Dict[str, int]]] = None
    fixed_lessons: Optional[List[FixedLesson]] = []
    facility_limits: Optional[Dict[str, int]] = {}
    require_full: Optional[bool] = True
    max_consecutive: Optional[int] = 4
    max_teacher_daily_hours: Optional[int] = 5
    total_hours: Optional[int] = 29
    time_limit: Optional[float] = 20.0


@app.get("/")
def read_root():
    return {"message": f"学校時間割最適化API v{VERSION}（固定コマ対応 + 押し出し修復版）"}


def teacher_key(name: str) -> str:
    """先生の識別キー（半角・全角スペースを除いた名前）"""
    return "".join(str(name or "").split())


def grade_of(class_name: str) -> str:
    """'1-2' → '1年'"""
    return f"{str(class_name).strip()[0]}年"


# ========== 問題データ ==========
class Problem:
    def __init__(self, req: ScheduleRequest):
        self.warnings: List[str] = []
        self.short_days = req.short_days if req.short_days else ["水"]
        self.master = req.subject_hours or DEFAULT_SUBJECT_MASTER

        # 施設上限：送られてきた教科だけ（基礎教科のデフォルト上限は付けない）
        self.facility = {s: int(v) for s, v in (req.facility_limits or {}).items() if int(v) > 0}

        # 教員
        self.teachers = []
        for t in req.teachers:
            classes = [c.strip() for c in t.classes if str(c).strip()]
            if t.id <= 0 or not classes:
                continue
            self.teachers.append({"id": int(t.id), "name": t.name.strip(),
                                  "subject": t.subject.strip(), "classes": classes,
                                  "hours": int(t.hours)})
        self.name_to_id = {t["name"]: t["id"] for t in self.teachers}
        # 先生の識別キー＝空白を除いた教員名（同じ先生が複数行＝複数教科でも同一人物）
        self.id_to_key = {t["id"]: teacher_key(t["name"]) for t in self.teachers}

        self.classes = sorted({c for t in self.teachers for c in t["classes"]})

        # 各クラスで使えるコマ
        self.slots = [(d, p) for d in DAYS for p in PERIODS
                      if not (d in self.short_days and p == 6)]

        # NG（先生キー, 曜日, 限）
        self.ng = set()
        for ng in req.ng_list or []:
            if ng.target_type == "teacher":
                try:
                    key = self.id_to_key.get(int(ng.target_id))
                    if key:
                        self.ng.add((key, ng.day, int(ng.period)))
                except ValueError:
                    pass

        # 学年一斉コマ
        self.group = {}  # (学年, 曜日, 限) → 教科
        for gs in req.group_slots or []:
            g = str(gs.grade).replace("年", "").strip() + "年"
            self.group[(g, gs.day, int(gs.period))] = gs.subject.strip()

        # 特別活動の担当（教科, クラス）→ (先生キー, 先生名)
        self.special_teacher = {}
        for st in req.special_teachers or []:
            key = teacher_key(st.teacher_name)
            self.special_teacher[(st.subject.strip(), st.class_name.strip())] = (key, st.teacher_name.strip())

        # 配置すべき授業（1コマ = 1ユニット）
        self.units = []           # dict(class, subject, tkey, tname)
        self.no_teacher = []      # 担当教員が見つからない授業
        for c in self.classes:
            g = grade_of(c)
            need = dict(self.master.get(g, {}))
            # マスタにない教科（選択教科など）は教員の週コマ数から推定
            for t in self.teachers:
                if c in t["classes"] and t["subject"] not in need:
                    need[t["subject"]] = t["hours"] // len(t["classes"])
            for subj, hours in need.items():
                if subj in SPECIAL_ACTIVITIES or hours <= 0:
                    continue
                teacher = next((t for t in self.teachers
                                if t["subject"] == subj and c in t["classes"]), None)
                if teacher is None:
                    for _ in range(hours):
                        self.no_teacher.append({"class": c, "subject": subj,
                                                "reason": "担当教員が未設定"})
                    continue
                for _ in range(hours):
                    self.units.append({"class": c, "subject": subj,
                                       "tkey": teacher_key(teacher["name"]), "tname": teacher["name"]})

        # ===== 固定コマ（手で決めた授業）=====
        # 学年一斉コマと重なるもの・枠外のものは除外し、その分の授業ユニットを減らす
        self.fixed = []
        group_cells = {(c, d, p) for c in self.classes for (g, d, p) in self.group if grade_of(c) == g}
        for fl in getattr(req, "fixed_lessons", None) or []:
            c, d, p, subj = fl.class_name.strip(), fl.day.strip(), int(fl.period), fl.subject.strip()
            if c not in self.classes or (d, p) not in self.slots or (c, d, p) in group_cells or not subj:
                continue
            if subj in SPECIAL_ACTIVITIES:
                tkey, tname = self.special_teacher.get((subj, c), (None, ""))
            else:
                t = next((t for t in self.teachers if t["subject"] == subj and c in t["classes"]), None)
                tkey, tname = (teacher_key(t["name"]), t["name"]) if t else (None, "")
            self.fixed.append({"class": c, "subject": subj, "tkey": tkey, "tname": tname, "slot": (d, p)})
            idx = next((i for i, u in enumerate(self.units) if u["class"] == c and u["subject"] == subj), None)
            if idx is not None:
                self.units.pop(idx)

        # 先生ごとの担当コマ数（配置の優先度に使う）
        load = {}
        for u in self.units:
            load[u["tkey"]] = load.get(u["tkey"], 0) + 1
        self.teacher_load = load

        # ===== 事前チェック：原理的に置けないコマ数（下限）を計算 =====
        # 下限に達したら、それ以上探しても改善しないので探索を打ち切る
        self.lower_bound = 0
        self.notes: List[str] = []
        blocked = {(c, d, p) for c in self.classes for (g, d, p) in self.group
                   if grade_of(c) == g and (d, p) in self.slots}
        blocked |= {(f["class"], f["slot"][0], f["slot"][1]) for f in self.fixed}

        for c in self.classes:
            n_units = sum(1 for u in self.units if u["class"] == c)
            capacity = len(self.slots) - sum(1 for (cc, d, p) in blocked if cc == c)
            if n_units > capacity:
                self.lower_bound += n_units - capacity
                self.warnings.append(
                    f"{c}: 必要コマ数 {n_units} が使えるコマ {capacity} を {n_units - capacity} 超えています（教科マスタを確認）")
            elif n_units < capacity:
                self.notes.append(f"{c}: 教科マスタの合計が週コマ数より {capacity - n_units} 少ないため、その分は空欄になります")

        for subj, lim in self.facility.items():
            need = sum(1 for u in self.units if u["subject"] == subj)
            if need == 0:
                continue
            need_classes = {u["class"] for u in self.units if u["subject"] == subj}
            cap = sum(min(lim, sum(1 for c in need_classes if (c, d, p) not in blocked))
                      for (d, p) in self.slots)
            if need > cap:
                self.lower_bound += need - cap
                self.warnings.append(
                    f"{subj}: 全クラスで週 {need} コマ必要ですが、施設上限 {lim} では最大 {cap} コマしか置けません"
                    f"（{need - cap} コマは必ず不足。施設上限か教科マスタを見直してください）")


# ========== 1回分の時間割（状態） ==========
class State:
    def __init__(self, prob: Problem):
        self.P = prob
        self.cell = {c: {s: None for s in prob.slots} for c in prob.classes}
        self.busy = {}       # (先生キー, 曜日, 限) → クラス
        self.fac = {}        # (教科, 曜日, 限) → 使用クラス数
        self.daycnt = {}     # (クラス, 曜日, 教科) → コマ数
        self.unplaced = []
        self.log = []        # 巻き戻し用の操作ログ

    # ---- 基本操作（すべてログに残す） ----
    def _put(self, u, c, slot, fixed=False, logging=True):
        d, p = slot
        if logging:
            self.log.append(("put", c, slot))
        self.cell[c][slot] = dict(u, fixed=fixed)
        if u["tkey"] is not None:
            self.busy[(u["tkey"], d, p)] = c
        self.fac[(u["subject"], d, p)] = self.fac.get((u["subject"], d, p), 0) + 1
        k = (c, d, u["subject"])
        self.daycnt[k] = self.daycnt.get(k, 0) + 1

    def _take(self, c, slot, logging=True):
        d, p = slot
        u = self.cell[c][slot]
        if logging:
            self.log.append(("take", c, slot, u))
        self.cell[c][slot] = None
        if u["tkey"] is not None and self.busy.get((u["tkey"], d, p)) == c:
            del self.busy[(u["tkey"], d, p)]
        self.fac[(u["subject"], d, p)] -= 1
        self.daycnt[(c, d, u["subject"])] -= 1
        return {k: v for k, v in u.items() if k != "fixed"}

    def rollback(self, mark):
        """ログを mark の時点まで逆再生して、状態を完全に元に戻す"""
        while len(self.log) > mark:
            op = self.log.pop()
            if op[0] == "put":
                self._take(op[1], op[2], logging=False)
            else:
                u = op[3]
                self._put({k: v for k, v in u.items() if k != "fixed"},
                          op[1], op[2], fixed=u["fixed"], logging=False)

    # ---- 制約チェック ----
    def ok_except_occupancy(self, u, slot, ignore_teacher=False):
        """セルの空き以外の制約（教員・NG・同日・連続・施設）を満たすか"""
        c, s, t = u["class"], u["subject"], u["tkey"]
        d, p = slot
        if (t, d, p) in self.P.ng:
            return False
        if not ignore_teacher and (t, d, p) in self.busy:
            return False
        if self.daycnt.get((c, d, s), 0) > 0:          # 同じ日に同じ教科は1コマまで
            return False
        for q in (p - 1, p + 1):                       # 連続禁止（同日制限の保険）
            n = self.cell[c].get((d, q))
            if n and n["subject"] == s:
                return False
        lim = self.P.facility.get(s)
        if lim is not None and self.fac.get((s, d, p), 0) >= lim:
            return False
        return True

    def can_place(self, u, slot):
        return self.cell[u["class"]][slot] is None and self.ok_except_occupancy(u, slot)

    # ---- 学年一斉コマの確保 ----
    def place_group_slots(self):
        for (g, d, p), subj in self.P.group.items():
            if (d, p) not in self.P.slots:
                continue
            for c in self.P.classes:
                if grade_of(c) != g or self.cell[c][(d, p)] is not None:
                    continue
                tkey, tname = self.P.special_teacher.get((subj, c), (None, ""))
                self._put({"class": c, "subject": subj, "tkey": tkey, "tname": tname},
                          c, (d, p), fixed=True)

    # ---- 固定コマ（手で決めた授業。制約チェックなしでそのまま置く）----
    def place_fixed(self):
        for f in self.P.fixed:
            c, slot = f["class"], f["slot"]
            if self.cell[c][slot] is not None:
                continue
            u = {k: f[k] for k in ("class", "subject", "tkey", "tname")}
            self._put(u, c, slot, fixed=True)

    # ---- 貪欲配置 ----
    def greedy(self, units):
        for u in units:
            cands = [s for s in self.P.slots if self.can_place(u, s)]
            if cands:
                self._put(u, u["class"], random.choice(cands))
            else:
                self.unplaced.append(u)

    # ---- 押し出し修復（ejection chain） ----
    def insert(self, u, depth, tabu, deadline):
        """u を置く。空きがなければ他の授業をどかして再配置（深さ depth まで）"""
        c = u["class"]
        slots = list(self.P.slots)
        random.shuffle(slots)
        for s in slots:
            if self.can_place(u, s):
                self._put(u, c, s)
                return True
        if depth == 0 or time.time() > deadline:
            return False

        for s in slots:
            if (c, s) in tabu:
                continue
            occ = self.cell[c][s]
            if occ is not None and occ["fixed"]:
                continue
            d, p = s
            # このコマで u の先生が他クラスを教えている場合、その授業もどかす
            other_c = self.busy.get((u["tkey"], d, p))
            if other_c is not None and other_c != c:
                oc = self.cell[other_c][s]
                if oc is None or oc["fixed"] or (other_c, s) in tabu:
                    continue
            if occ is None and other_c is None:
                continue  # 空いていて先生も空き → 別の制約で置けないコマ

            mark = len(self.log)
            removed = []
            if occ is not None:
                removed.append((c, s, self._take(c, s)))
            if other_c is not None and other_c != c:
                removed.append((other_c, s, self._take(other_c, s)))

            if self.can_place(u, s):
                self._put(u, c, s)
                new_tabu = tabu | {(c, s)} | {(rc, rs) for rc, rs, _ in removed}
                if all(self.insert(ru, depth - 1, new_tabu, deadline) for _, _, ru in removed):
                    return True
            self.rollback(mark)   # 失敗 → 連鎖で動いた分も含めて完全に元に戻す
        return False

    def repair(self, deadline, max_depth=3):
        for depth in range(1, max_depth + 1):
            if not self.unplaced or time.time() > deadline:
                break
            rest = []
            random.shuffle(self.unplaced)
            for u in self.unplaced:
                if not self.insert(u, depth, frozenset(), deadline):
                    rest.append(u)
                self.log.clear()   # 確定した分のログは不要
            self.unplaced = rest

    # ---- 出力 ----
    def to_schedule(self):
        out = []
        for c in self.P.classes:
            for (d, p) in self.P.slots:
                v = self.cell[c][(d, p)]
                if v:
                    out.append({"class": c, "day": d, "period": p,
                                "subject": v["subject"], "teacher_name": v["tname"]})
        return out


def build_one(prob: Problem, deadline: float) -> State:
    st = State(prob)
    st.place_group_slots()
    st.place_fixed()
    units = list(prob.units)
    random.shuffle(units)
    # 施設上限のある教科 → 担当コマの多い先生 の順に先に置く（揺らぎ付き）
    units.sort(key=lambda u: (u["subject"] not in prob.facility,
                              -prob.teacher_load[u["tkey"]] + random.random() * 3))
    st.greedy(units)
    st.log.clear()
    st.repair(deadline)
    return st


# ========== 独立した検証（生成結果を別ロジックで数え直す） ==========
def count_violations(schedule, prob: Problem):
    v = {"教員被り": 0, "施設上限": 0, "連続配置": 0, "同日重複": 0}
    by_slot = {}
    for it in schedule:
        by_slot.setdefault((it["day"], it["period"]), []).append(it)
    for (d, p), items in by_slot.items():
        seen = {}
        for it in items:
            if it["subject"] in SPECIAL_ACTIVITIES or not it["teacher_name"]:
                continue
            seen[it["teacher_name"]] = seen.get(it["teacher_name"], 0) + 1
        v["教員被り"] += sum(n - 1 for n in seen.values() if n > 1)
        for subj, lim in prob.facility.items():
            n = sum(1 for it in items if it["subject"] == subj)
            if n > lim:
                v["施設上限"] += n - lim
    grid = {(it["class"], it["day"], it["period"]): it["subject"] for it in schedule}
    for c in prob.classes:
        for d in DAYS:
            subs = [grid.get((c, d, p)) for p in PERIODS]
            for a, b in zip(subs, subs[1:]):
                if a and a == b:
                    v["連続配置"] += 1
            counts = {}
            for s in subs:
                if s and s not in SPECIAL_ACTIVITIES:
                    counts[s] = counts.get(s, 0) + 1
            v["同日重複"] += sum(n - 1 for n in counts.values() if n > 1)
    return v


@app.post("/optimize")
def optimize_schedule(request: ScheduleRequest):
    try:
        prob = Problem(request)
        if not prob.teachers:
            return {"schedule": [], "status": "ERROR", "message": "有効な教員データがありません"}

        budget = max(3.0, min(float(request.time_limit or 20.0), 25.0))
        start = time.time()
        deadline = start + budget
        best, trials = None, 0
        while time.time() < deadline:
            trials += 1
            st = build_one(prob, deadline)
            if best is None or len(st.unplaced) < len(best.unplaced):
                best = st
                print(f"DEBUG: 試行{trials} 未配置 {len(st.unplaced)}")
            if len(best.unplaced) <= prob.lower_bound:
                break   # 原理的な下限に到達（これ以上は改善しない）

        schedule = best.to_schedule()
        violations = count_violations(schedule, prob)
        total_slots = len(prob.slots) * len(prob.classes)
        fill_rate = len(schedule) / total_slots if total_slots else 0.0

        unplaced = [{"class": u["class"], "subject": u["subject"],
                     "teacher_name": u["tname"], "reason": "制約により配置できず"}
                    for u in best.unplaced] + prob.no_teacher
        unplaced.sort(key=lambda x: (x["class"], x["subject"]))

        msg = f"時間割生成完了: {len(schedule)}/{total_slots}コマ（充填率 {fill_rate:.1%}）"
        if unplaced:
            msg += f" / 未配置 {len(unplaced)}コマ"
        print(f"DEBUG: {msg} 試行{trials}回 {time.time() - start:.1f}秒 違反={violations}")

        return {
            "schedule": schedule,
            "status": "SUCCESS" if not unplaced else "PARTIAL",
            "message": msg,
            "fill_rate": fill_rate,
            "unplaced": unplaced,
            "violations": violations,
            "warnings": prob.warnings,
            "notes": sorted(set(prob.notes)),
            "lower_bound": prob.lower_bound,
            "trials": trials,
            "version": VERSION,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"schedule": [], "status": "ERROR", "message": f"エラー: {e}"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
