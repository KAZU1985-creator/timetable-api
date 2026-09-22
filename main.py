// ==========================================
// 時間割作成システム（設計版 v2.2.14）
// 教員が複数クラスを担当・正確な週コマ数
// ==========================================

const API_URL = 'https://timetable-api-uoum.onrender.com/optimize';

function onOpen() {
  const ui = SpreadsheetApp.getUi();
  ui.createMenu('🚀【時間割作成メニュー】')
    .addItem('🎓【最初はこれ！】サンプルで全自動デモ実行', 'runFullSampleDemo')
    .addSeparator()
    .addItem('1. 初期シートを自動作成・セットアップ', 'setupSheets')
    .addSeparator()
    .addItem('★ 2. 一括生成を実行（時間割を作る）★推奨★', 'generateTimetable')
    .addSeparator()
    .addItem('3. 時間割の被り・重複・制約チェック', 'checkTimetableErrors')
    .addSeparator()
    .addItem('【デバッグ用】手動で修復を1回実行', 'autoFixErrors')
    .addToUi();
}

function runFullSampleDemo() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ui = SpreadsheetApp.getUi();

  try {
    ui.alert('🎓 サンプル練習を開始します！');
    setupSheets();
    Utilities.sleep(1000);
    loadSampleTeacherData();
    Utilities.sleep(1000);
    runSampleScheduleGeneration();
  } catch (e) {
    ui.alert('❌ エラー：' + e.toString());
    Logger.log(e);
  }
}

function runSampleScheduleGeneration() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ui = SpreadsheetApp.getUi();

  try {
    const { payload, groupSlots, specialTeachers } = collectInputData(ss);

    if (!payload.teachers || payload.teachers.length === 0) {
      ui.alert('警告', '「教員設定」シートに教員のデータが入力されていません。', ui.ButtonSet.OK);
      return;
    }

    ss.toast('APIに送信中...', '処理中', -1);

    const options = {
      'method': 'post',
      'contentType': 'application/json',
      'payload': JSON.stringify(payload),
      'muteHttpExceptions': true
    };

    const response = UrlFetchApp.fetch(API_URL, options);
    const responseCode = response.getResponseCode();
    const responseText = response.getContentText();

    let result = {};
    try {
      result = JSON.parse(responseText);
    } catch (e) {
      throw new Error(`レスポンス解析エラー (HTTP ${responseCode}):\n${responseText}`);
    }

    if (responseCode !== 200) {
      throw new Error(`APIエラー (HTTP ${responseCode}):\n${result.detail || responseText}`);
    }

    let timetableData = result.schedule || result.timetable || [];
    timetableData = enforceGroupSlotsAndFixGaps(ss, timetableData, groupSlots, specialTeachers);

    writeTimetableToSheet(ss, timetableData, payload.teachers);
    writeClassTimetableToSheet(ss, timetableData);

    ss.toast('✅ 時間割生成完了！\n\n自動修復を3回実行中...', '処理中', -1);
    
    // ========== ここから自動修復×3 ==========
    for (let fixCount = 1; fixCount <= 3; fixCount++) {
      Utilities.sleep(500);  // 少し待機
      autoFixErrorsSilent();  // サイレント版（トーストなし）
      ss.toast(`自動修復中... ${fixCount}/3`, '処理中', -1);
    }
    // ========== 自動修復終了 ==========

    ss.toast('✅ 時間割生成 + 自動修復が完了しました！\nサンプルを確認してください。', '完了', 5);

  } catch (error) {
    SpreadsheetApp.getUi().alert('❌ エラー：' + error.message);
    Logger.log(error);
  }
}

function generateTimetable() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ui = SpreadsheetApp.getUi();

  try {
    const { payload, groupSlots, specialTeachers } = collectInputData(ss);

    if (!payload.teachers || payload.teachers.length === 0) {
      ui.alert('エラー', '「教員設定」シートに教員データが入力されていません。', ui.ButtonSet.OK);
      return;
    }

    console.log('DEBUG: 送信する教員データ:', JSON.stringify(payload.teachers, null, 2));

    ss.toast('APIに送信中...', '処理中', -1);

    const options = {
      'method': 'post',
      'contentType': 'application/json',
      'payload': JSON.stringify(payload),
      'muteHttpExceptions': true
    };

    const response = UrlFetchApp.fetch(API_URL, options);
    const responseCode = response.getResponseCode();
    const responseText = response.getContentText();

    let result = {};
    try {
      result = JSON.parse(responseText);
    } catch (e) {
      throw new Error(`レスポンス解析エラー (HTTP ${responseCode}):\n${responseText}`);
    }

    if (responseCode !== 200) {
      // バリデーションエラーの詳細を表示
      let errorMsg = `APIエラー (HTTP ${responseCode})\n`;
      if (result.detail && Array.isArray(result.detail)) {
        // Pydanticバリデーションエラー
        result.detail.forEach((err, idx) => {
          const field = err.loc ? err.loc.join('.') : 'unknown';
          const msg = err.msg || 'unknown error';
          errorMsg += `\n[${idx + 1}] フィールド: ${field}\n   エラー: ${msg}`;
        });
      } else {
        errorMsg += result.detail || responseText;
      }
      throw new Error(errorMsg);
    }

    let timetableData = result.schedule || result.timetable || [];
    timetableData = enforceGroupSlotsAndFixGaps(ss, timetableData, groupSlots, specialTeachers);

    writeTimetableToSheet(ss, timetableData, payload.teachers);
    writeClassTimetableToSheet(ss, timetableData);

    ss.toast('✅ 時間割生成完了！\n\n自動修復を3回実行中...', '処理中', -1);
    
    // ========== ここから自動修復×3 ==========
    for (let fixCount = 1; fixCount <= 3; fixCount++) {
      Utilities.sleep(500);  // 少し待機
      autoFixErrorsSilent();  // サイレント版（トーストなし）
      ss.toast(`自動修復中... ${fixCount}/3`, '処理中', -1);
    }
    // ========== 自動修復終了 ==========

    ss.toast('✅ 時間割生成 + 自動修復が完了しました！\n内容を確認してください。', '完了', 5);

  } catch (error) {
    SpreadsheetApp.getUi().alert('❌ エラー：' + error.message);
    Logger.log(error);
  }
}

function setupSheets() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ui = SpreadsheetApp.getUi();

  let configSheet = ss.getSheetByName('設定');
  if (!configSheet) configSheet = ss.insertSheet('設定');
  else configSheet.clear();

  // ========== A列：クラス一覧 ==========
  configSheet.getRange('A:A').setNumberFormat('@');
  const classListValues = [['クラス一覧']];
  for (let grade = 1; grade <= 3; grade++) {
    for (let c = 1; c <= 3; c++) classListValues.push([`${grade}-${c}`]);
  }
  configSheet.getRange(1, 1, classListValues.length, 1).setValues(classListValues);
  configSheet.getRange(1, 1).setBackground('#434343').setFontColor('#ffffff').setFontWeight('bold');
  configSheet.setColumnWidth(1, 100);

  // ========== B〜E列：教科マスタ ==========
  const subjectMaster = [
    ['教科一覧', '1年コマ数', '2年コマ数', '3年コマ数'],
    ['国語', 4, 4, 4],
    ['社会', 4, 4, 4],
    ['数学', 4, 4, 4],
    ['理科', 3, 3, 3],
    ['英語', 3, 3, 3],
    ['音楽', 1, 1, 1],
    ['美術', 1, 1, 1],
    ['保体', 1, 1, 1],
    ['技術', 1, 1, 1],
    ['家庭', 1, 1, 1],
    ['学活', 1, 1, 1],
    ['総合', 1, 1, 1],
    ['道徳', 1, 1, 1]
  ];
  configSheet.getRange(1, 2, subjectMaster.length, 4).setValues(subjectMaster);
  configSheet.getRange('B1:E1').setBackground('#434343').setFontColor('#ffffff').setFontWeight('bold').setHorizontalAlignment('center');
  for (let col = 2; col <= 5; col++) configSheet.setColumnWidth(col, 85);

  // ========== G〜J列：学年一斉コマ設定 ==========
  configSheet.getRange('G1:J1').merge().setValue('【学年一斉コマ設定】（道徳1限 / 学活・総合6限）')
    .setBackground('#2F5597').setFontColor('#FFFFFF').setFontWeight('bold').setHorizontalAlignment('center');
  configSheet.getRange('G2:J2').setValues([['対象学年', '曜日', '時限', '実施教科']])
    .setBackground('#D9E1F2').setFontWeight('bold').setHorizontalAlignment('center');

  const sampleGroupData = [
    ['1年', '木', 1, '道徳'],
    ['2年', '金', 1, '道徳'],
    ['3年', '月', 1, '道徳'],
    ['1年', '月', 6, '学活'],
    ['2年', '火', 6, '学活'],
    ['3年', '水', 5, '学活'],
    ['1年', '金', 6, '総合'],
    ['2年', '月', 6, '総合'],
    ['3年', '木', 6, '総合']
  ];
  configSheet.getRange(3, 7, sampleGroupData.length, 4).setValues(sampleGroupData).setHorizontalAlignment('center');
  for (let col = 7; col <= 10; col++) configSheet.setColumnWidth(col, 90);

  // ========== K1〜L3列：5時間授業設定 ==========
  configSheet.getRange('K1:L1').merge().setValue('【5時間授業（6限カット）】')
    .setBackground('#2F5597').setFontColor('#FFFFFF').setFontWeight('bold').setHorizontalAlignment('center');
  configSheet.getRange('K2').setValue('5時間授業の曜日①').setBackground('#D9E1F2').setFontWeight('bold').setHorizontalAlignment('center');
  configSheet.getRange('L2').setValue('水').setHorizontalAlignment('center').setFontWeight('bold');
  configSheet.getRange('K3').setValue('5時間授業の曜日②').setBackground('#D9E1F2').setFontWeight('bold').setHorizontalAlignment('center');
  configSheet.getRange('L3').setValue('なし').setHorizontalAlignment('center').setFontWeight('bold');

  const daysValidation = SpreadsheetApp.newDataValidation().requireValueInList(['月', '火', '水', '木', '金', 'なし'], true).build();
  configSheet.getRange('L2:L3').setDataValidation(daysValidation);

  // ========== K5〜L11：特別施設・体育施設上限 ==========
  configSheet.getRange('K5:L5').merge().setValue('【特別施設・体育施設上限】')
    .setBackground('#2F5597').setFontColor('#FFFFFF').setFontWeight('bold').setHorizontalAlignment('center');
  configSheet.getRange('K6:L6').setValues([['施設/教科', '同時上限']]).setBackground('#D9E1F2').setFontWeight('bold').setHorizontalAlignment('center');
  
  const facilityData = [
    ['保体', 3],
    ['理科', 3],
    ['音楽', 3],
    ['美術', 3],
    ['技術', 3]
  ];
  
  configSheet.getRange(7, 11, facilityData.length, 2).setValues(facilityData).setHorizontalAlignment('center');
  configSheet.getRange(7, 11, facilityData.length, 2).setBackground('#FFF2CC');
  
  configSheet.setColumnWidth(11, 120);
  configSheet.setColumnWidth(12, 100);

  // ========== K13:L13：週コマ数 ==========
  configSheet.getRange('K13').setValue('週あたりのコマ数').setBackground('#2F5597').setFontColor('#FFFFFF').setFontWeight('bold').setHorizontalAlignment('center');
  configSheet.getRange('L13').setValue(29).setHorizontalAlignment('center').setFontWeight('bold').setFontSize(14).setBackground('#FFFFCC');

  // ========== 教員設定シート ==========
  let teacherSheet = ss.getSheetByName('教員設定');
  if (!teacherSheet) teacherSheet = ss.insertSheet('教員設定');
  else teacherSheet.clear();

  teacherSheet.getRange('F:F').setNumberFormat('@').setWrap(true);
  teacherSheet.getRange('H:J').setNumberFormat('@').setWrap(true);

  teacherSheet.getRange('A1:J1').merge()
    .setValue('💡 【制度設計】教員が複数クラスを担当する場合、「担当クラス」に全クラスを入力してください。週コマ数は自動計算されます。')
    .setBackground('#FFF2CC').setFontColor('#7F6000').setFontWeight('bold').setVerticalAlignment('middle');
  teacherSheet.setRowHeight(1, 50);

  const teacherHeaders = [['教員ID', '教員名', '所属学年', '担任・副任', '教科', '担当クラス（複数可）', '週コマ数（自動計算）', '道徳担当', '学活担当', '総合担当']];
  teacherSheet.getRange(2, 1, 1, 10).setValues(teacherHeaders)
    .setBackground('#1C4587').setFontColor('#FFFFFF').setFontWeight('bold').setHorizontalAlignment('center');

  const maxTeachers = 25;
  const idRows = [];
  const formulas = [];
  for (let i = 1; i <= maxTeachers; i++) {
    const rowNum = i + 2;
    idRows.push([i]); 
    formulas.push([`=CALCULATE_TEACHER_HOURS(E${rowNum}, F${rowNum})`]);
  }
  teacherSheet.getRange(3, 1, maxTeachers, 1).setValues(idRows).setHorizontalAlignment('center');
  teacherSheet.getRange(3, 7, maxTeachers, 1).setFormulas(formulas).setHorizontalAlignment('center');

  const classRange = configSheet.getRange(2, 1, classListValues.length - 1, 1);
  const subjectRange = configSheet.getRange(2, 2, subjectMaster.length - 1, 1);
  const classValidation = SpreadsheetApp.newDataValidation().requireValueInRange(classRange).build();
  const subjectValidation = SpreadsheetApp.newDataValidation().requireValueInRange(subjectRange).build();
  const gradeValidation = SpreadsheetApp.newDataValidation().requireValueInList(['1年', '2年', '3年'], true).build();

  teacherSheet.getRange(3, 3, maxTeachers, 1).setDataValidation(gradeValidation);
  teacherSheet.getRange(3, 5, maxTeachers, 1).setDataValidation(subjectValidation);
  teacherSheet.getRange(3, 6, maxTeachers, 1).setDataValidation(classValidation);
  teacherSheet.getRange(3, 8, maxTeachers, 3).setDataValidation(classValidation);

  teacherSheet.setColumnWidth(1, 60);
  teacherSheet.setColumnWidth(2, 100);
  teacherSheet.setColumnWidth(3, 80);
  teacherSheet.setColumnWidth(4, 110);
  teacherSheet.setColumnWidth(5, 90);
  teacherSheet.setColumnWidth(6, 280);
  teacherSheet.setColumnWidth(7, 120);
  teacherSheet.setColumnWidth(8, 110);
  teacherSheet.setColumnWidth(9, 110);
  teacherSheet.setColumnWidth(10, 110);

  // ========== NG設定シート ==========
  let ngSheet = ss.getSheetByName('NG設定');
  if (!ngSheet) ngSheet = ss.insertSheet('NG設定');
  else ngSheet.clear();

  const days = ['月', '火', '水', '木', '金'];
  const ngHeaders = ['教員ID', '教員名'];
  days.forEach(d => { for (let p = 1; p <= 6; p++) ngHeaders.push(`${d}${p}`); });

  ngSheet.getRange(1, 1, 1, ngHeaders.length).setValues([ngHeaders])
    .setBackground('#CC0000').setFontColor('#FFFFFF').setFontWeight('bold').setHorizontalAlignment('center');

  const sampleNgCount = 25;
  const sampleNgRows = [];
  for (let i = 1; i <= sampleNgCount; i++) sampleNgRows.push([i, `教員${i}`]);
  ngSheet.getRange(2, 1, sampleNgRows.length, 2).setValues(sampleNgRows);

  for (let c = 3; c <= ngHeaders.length; c++) ngSheet.setColumnWidth(c, 40);
  ngSheet.setColumnWidth(1, 70);
  ngSheet.setColumnWidth(2, 110);

  if (!ss.getSheetByName('完成時間割')) ss.insertSheet('完成時間割');
  if (!ss.getSheetByName('クラス別時間割')) ss.insertSheet('クラス別時間割');

  ui.alert('初期セットアップ完了', '【正しい制度設計】で初期化が完了しました！\n\n教員が複数クラスを担当する場合、「担当クラス」に全クラスを入力してください。', ui.ButtonSet.OK);
}

function loadSampleTeacherData() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ui = SpreadsheetApp.getUi();
  const teacherSheet = ss.getSheetByName('教員設定');

  if (!teacherSheet) {
    ui.alert('エラー', '先に「1. 初期シートを自動作成・セットアップ」を実行してください。', ui.ButtonSet.OK);
    return;
  }

  const sampleData = [
    [1, "佐藤 太郎", "1年", "1-1担任", "国語", "1-1, 1-2, 1-3", "", "1-1", "1-1", "1-1"],
    [2, "山本 雅志", "1年", "1-2担任", "社会", "1-1, 1-2, 1-3", "", "1-2", "1-2", "1-2"],
    [3, "小林 裕樹", "1年", "1-3担任", "数学", "1-1, 1-2, 1-3", "", "1-3", "1-3", "1-3"],
    [4, "佐々木 隆", "1年", "副任", "理科", "1-1, 1-2, 1-3", "", "", "", ""],
    [5, "斎藤 雄太", "1年", "副任", "英語", "1-1, 1-2, 1-3", "", "", "", ""],
    [6, "鈴木 一郎", "2年", "2-1担任", "国語", "2-1, 2-2, 2-3", "", "2-1", "2-1", "2-1"],
    [7, "田中 健一", "2年", "2-2担任", "社会", "2-1, 2-2, 2-3", "", "2-2", "2-2", "2-2"],
    [8, "加藤 大介", "2年", "2-3担任", "数学", "2-1, 2-2, 2-3", "", "2-3", "2-3", "2-3"],
    [9, "山口 達也", "2年", "副任", "理科", "2-1, 2-2, 2-3", "", "", "", ""],
    [10, "松本 潤", "2年", "副任", "英語", "2-1, 2-2, 2-3", "", "", "", ""],
    [11, "高橋 健太", "3年", "3-1担任", "国語", "3-1, 3-2, 3-3", "", "3-1", "3-1", "3-1"],
    [12, "中村 拓也", "3年", "3-2担任", "社会", "3-1, 3-2, 3-3", "", "3-2", "3-2", "3-2"],
    [13, "吉田 哲也", "3年", "3-3担任", "数学", "3-1, 3-2, 3-3", "", "3-3", "3-3", "3-3"],
    [14, "田中 美咲", "3年", "副任", "理科", "3-1, 3-2, 3-3", "", "", "", ""],
    [15, "井上 和也", "3年", "副任", "英語", "3-1, 3-2, 3-3", "", "", "", ""],
    [16, "村上 美咲", "1年", "副任", "音楽", "1-1, 1-2, 1-3, 2-1, 2-2, 2-3, 3-1, 3-2, 3-3", "", "", "", ""],
    [17, "石井 奈央", "1年", "副任", "美術", "1-1, 1-2, 1-3, 2-1, 2-2, 2-3, 3-1, 3-2, 3-3", "", "", "", ""],
    [18, "遠藤 健司", "2年", "副任", "保体", "1-1, 1-2, 1-3, 2-1, 2-2, 2-3, 3-1, 3-2, 3-3", "", "", "", ""],
    [19, "太田 工", "2年", "副任", "技術", "1-1, 1-2, 1-3, 2-1, 2-2, 2-3, 3-1, 3-2, 3-3", "", "", "", ""],
    [20, "原田 花子", "3年", "副任", "家庭", "1-1, 1-2, 1-3, 2-1, 2-2, 2-3, 3-1, 3-2, 3-3", "", "", "", ""]
  ];

  for (let i = 0; i < sampleData.length; i++) {
    const rowNum = i + 3;
    sampleData[i][6] = `=CALCULATE_TEACHER_HOURS(E${rowNum}, F${rowNum})`;
  }

  teacherSheet.getRange(3, 1, sampleData.length, 10).setValues(sampleData);
  ui.alert('完了', '教員20名のサンプルデータを入力しました！\n\n【確認事項】\n- 各教員が複数クラスを担当しています\n- 週コマ数が自動計算されています\n- 確認: 「F6」セルなどで担当クラスを確認してください', ui.ButtonSet.OK);
}

function collectInputData(ss) {
  const teacherSheet = ss.getSheetByName('教員設定');
  const ngSheet = ss.getSheetByName('NG設定');
  const configSheet = ss.getSheetByName('設定');

  const teachersData = [];
  const ngData = [];
  const groupSlots = [];
  const specialTeachers = [];
  const facilityLimits = {};

  if (teacherSheet && teacherSheet.getLastRow() > 2) {
    const numRows = teacherSheet.getLastRow() - 2;
    const displayValues = teacherSheet.getRange(3, 1, numRows, 10).getDisplayValues();

    displayValues.forEach((row, idx) => {
      const teacherId = String(row[0] || '').trim();
      const teacherName = String(row[1] || '').trim();
      const subjectName = String(row[4] || '').trim();
      const rawClassName = String(row[5] || '').trim();
      const totalHours = parseInt(row[6] || '0', 10);

      const moralClass = String(row[7] || '').trim();
      const gakkatsuClass = String(row[8] || '').trim();
      const sogouClass = String(row[9] || '').trim();

      if (!teacherId || !teacherName || !subjectName || !rawClassName || totalHours <= 0) {
        return;
      }

      const classList = rawClassName.split(/[,、\s]+/).map(c => normalizeClassName(c)).filter(Boolean);
      
      if (classList.length > 0) {
        // ★厳密なバリデーション
        const finalId = Number(teacherId);
        if (isNaN(finalId) || finalId <= 0) {
          console.warn(`DEBUG: 無効な教員ID: "${teacherId}"`);
          return;  // スキップ
        }
        
        if (totalHours <= 0 || isNaN(totalHours)) {
          console.warn(`DEBUG: 無効な週コマ数: "${row[6]}", 計算値: ${totalHours}`);
          return;
        }
        
        teachersData.push({
          id: finalId,
          name: teacherName,
          subject: subjectName,
          classes: classList,  // リスト形式
          hours: totalHours
        });
      }

      if (moralClass) {
        moralClass.split(/[,、\s]+/).map(c => normalizeClassName(c)).filter(Boolean).forEach(c => {
          specialTeachers.push({ subject: '道徳', teacherName, className: c });
        });
      }
      if (gakkatsuClass) {
        gakkatsuClass.split(/[,、\s]+/).map(c => normalizeClassName(c)).filter(Boolean).forEach(c => {
          specialTeachers.push({ subject: '学活', teacherName, className: c });
        });
      }
      if (sogouClass) {
        sogouClass.split(/[,、\s]+/).map(c => normalizeClassName(c)).filter(Boolean).forEach(c => {
          specialTeachers.push({ subject: '総合', teacherName, className: c });
        });
      }
    });
  }

  if (ngSheet && ngSheet.getLastRow() > 1 && ngSheet.getLastColumn() > 2) {
    const ngLastRow = ngSheet.getLastRow();
    const ngLastCol = ngSheet.getLastColumn();
    const ngValues = ngSheet.getRange(1, 1, ngLastRow, ngLastCol).getValues();
    const days = ['月', '火', '水', '木', '金'];

    for (let r = 1; r < ngLastRow; r++) {
      const teacherId = String(ngValues[r][0] || '').trim();
      if (!teacherId) continue;

      for (let c = 2; c < ngLastCol; c++) {
        const val = String(ngValues[r][c] || '').trim();
        if (val === '✕' || val === 'x' || val === 'X' || val === 'NG') {
          const colHeader = String(ngValues[0][c] || '').trim();
          const day = colHeader.charAt(0);
          const period = parseInt(colHeader.slice(1), 10);

          if (days.includes(day) && !isNaN(period)) {
            ngData.push({ target_type: 'teacher', target_id: teacherId, day: day, period: period });
          }
        }
      }
    }
  }

  if (configSheet && configSheet.getLastRow() >= 3) {
    const gValues = configSheet.getRange(3, 7, configSheet.getLastRow() - 2, 4).getValues();
    gValues.forEach(gRow => {
      let grade = String(gRow[0] || '').trim().replace('年', '');
      const day = String(gRow[1] || '').trim();
      const period = parseInt(gRow[2] || '0', 10);
      const subject = String(gRow[3] || '').trim();

      if (grade && day && period && subject) {
        groupSlots.push({ grade, day, period, subject });
      }
    });

    const facValues = configSheet.getRange(7, 11, 5, 2).getValues();
    facValues.forEach(fRow => {
      const subj = String(fRow[0] || '').trim();
      const limit = parseInt(fRow[1] || '0', 10);
      if (subj && limit > 0) facilityLimits[subj] = limit;
    });
  }

  let totalWeeklyHours = 29;
  if (configSheet) {
    const weeklyHoursVal = configSheet.getRange('L13').getValue();
    if (weeklyHoursVal) {
      totalWeeklyHours = Number(weeklyHoursVal) || 29;
    }
  }

  return {
    payload: {
      teachers: teachersData,
      ng_list: ngData,
      group_slots: groupSlots,
      max_consecutive: 4,
      max_teacher_daily_hours: 5,
      facility_limits: facilityLimits,
      total_hours: totalWeeklyHours,
      time_limit: 60.0
    },
    groupSlots,
    specialTeachers
  };
}

function normalizeClassName(val) {
  if (val === null || val === undefined) return '';
  if (val instanceof Date) return `${val.getMonth() + 1}-${val.getDate()}`;
  let str = String(val).trim();
  if (str.match(/^\d{5}(\.\d+)?$/)) {
    const num = parseFloat(str);
    const date = new Date((num - (25567 + 2)) * 86400 * 1000);
    return `${date.getMonth() + 1}-${date.getDate()}`;
  }
  return str;
}

function getShortDays(ss) {
  const configSheet = ss.getSheetByName('設定');
  const validDays = ['月', '火', '水', '木', '金'];
  const shortDays = [];

  if (configSheet) {
    const val1 = String(configSheet.getRange('L2').getValue() || '').trim();
    const val2 = String(configSheet.getRange('L3').getValue() || '').trim();

    if (validDays.includes(val1)) shortDays.push(val1);
    if (validDays.includes(val2) && val2 !== val1) shortDays.push(val2);
  }

  if (shortDays.length === 0) return ['水'];
  return shortDays;
}

function CALCULATE_TEACHER_HOURS(subject, classStr) {
  if (!subject || !classStr) return "";
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const configSheet = ss.getSheetByName('設定');
  if (!configSheet) return 0;

  const lastRow = configSheet.getLastRow();
  if (lastRow < 2) return 0;

  const subjectData = configSheet.getRange(2, 2, lastRow - 1, 4).getValues();
  let g1Hours = 0, g2Hours = 0, g3Hours = 0;
  let found = false;

  for (let i = 0; i < subjectData.length; i++) {
    if (String(subjectData[i][0]).trim() === String(subject).trim()) {
      g1Hours = Number(subjectData[i][1]) || 0;
      g2Hours = Number(subjectData[i][2]) || 0;
      g3Hours = Number(subjectData[i][3]) || 0;
      found = true;
      break;
    }
  }
  if (!found) return 0;

  const rawClasses = String(classStr).split(/[,、\s]+/);
  let totalHours = 0;

  rawClasses.forEach(rawC => {
    const cName = normalizeClassName(rawC);
    const gradeMatch = cName.match(/^([1-3])/);
    if (gradeMatch) {
      const grade = gradeMatch[1];
      if (grade === '1') totalHours += g1Hours;
      else if (grade === '2') totalHours += g2Hours;
      else if (grade === '3') totalHours += g3Hours;
    }
  });

  return totalHours;
}

function enforceGroupSlotsAndFixGaps(ss, timetableData, groupSlots, specialTeachers) {
  if (!groupSlots || groupSlots.length === 0) return timetableData;

  const shortDays = getShortDays(ss);
  const configSheet = ss.getSheetByName('設定');
  let classList = [];
  if (configSheet && configSheet.getLastRow() > 1) {
    classList = configSheet.getRange(2, 1, configSheet.getLastRow() - 1, 1).getDisplayValues().map(r => String(r[0] || '').trim()).filter(Boolean);
  }

  const days = ['月', '火', '水', '木', '金'];
  const periods = [1, 2, 3, 4, 5, 6];

  const grid = {};
  classList.forEach(c => {
    grid[c] = {};
    days.forEach(d => {
      grid[c][d] = {};
      periods.forEach(p => { grid[c][d][p] = null; });
    });
  });

  timetableData.forEach(item => {
    const cName = normalizeClassName(item['class'] || item.className);
    const day = String(item.day || '').trim();
    const period = parseInt(item.period || 0, 10);
    if (grid[cName] && grid[cName][day] && grid[cName][day][period] !== undefined) {
      grid[cName][day][period] = item;
    }
  });

  groupSlots.forEach(gs => {
    const targetGrade = String(gs.grade).replace('年', '');
    const targetDay = gs.day;
    const targetPeriod = gs.period;
    const targetSubject = gs.subject;

    classList.forEach(cName => {
      if (cName.startsWith(targetGrade + '-')) {
        const displacedItem = grid[cName][targetDay][targetPeriod];

        let assignedTeacher = targetSubject;
        const matchTeacher = specialTeachers.find(s => s.subject === targetSubject && s.className === cName);
        if (matchTeacher) {
          assignedTeacher = matchTeacher.teacherName;
        }

        const newItem = {
          'class': cName,
          'day': targetDay,
          'period': targetPeriod,
          'subject': targetSubject,
          'teacher_name': assignedTeacher
        };

        grid[cName][targetDay][targetPeriod] = newItem;
      }
    });
  });

  const updatedTimetable = [];
  classList.forEach(c => {
    days.forEach(d => {
      periods.forEach(p => {
        if (grid[c][d][p]) updatedTimetable.push(grid[c][d][p]);
      });
    });
  });

  return updatedTimetable;
}

function writeTimetableToSheet(ss, timetableData, teachersList) {
  let outputSheet = ss.getSheetByName('完成時間割');
  if (!outputSheet) outputSheet = ss.insertSheet('完成時間割');
  else outputSheet.clear();

  const shortDays = getShortDays(ss);
  const days = ['月', '火', '水', '木', '金'];
  const periods = [1, 2, 3, 4, 5, 6];
  const cleanStr = (s) => String(s || '').replace(/\s+/g, '').trim();

  const slotMap = {};
  (timetableData || []).forEach(item => {
    const teacherName = cleanStr(item.teacher_name || item.teacher);
    const subject = cleanStr(item.subject);
    const day = cleanStr(item.day);
    const period = parseInt(item.period || 0, 10);
    const className = item['class'] || item.className || '';

    if (teacherName && day && period) {
      slotMap[`${teacherName}_${subject}_${day}_${period}`] = className;
      slotMap[`${teacherName}_${day}_${period}`] = className;
    }
  });

  const teacherSubjectPairs = [];
  const pairSeen = new Set();

  (teachersList || []).forEach(t => {
    const cName = cleanStr(t.name);
    const cSubj = cleanStr(t.subject);
    const pairKey = `${cName}_${cSubj}`;

    if (!pairSeen.has(pairKey)) {
      pairSeen.add(pairKey);
      teacherSubjectPairs.push({ rawName: t.name, rawSubject: t.subject, cleanName: cName, cleanSubject: cSubj });
    }
  });

  if (teacherSubjectPairs.length === 0) return;

  const row1 = ['教科', '担当教員'];
  days.forEach(day => { row1.push(day); for (let i = 0; i < 5; i++) row1.push(''); });
  const row2 = ['', ''];
  days.forEach(() => { periods.forEach(p => row2.push(p)); });

  const dataRows = [row1, row2];

  teacherSubjectPairs.forEach(t => {
    const row = [t.rawSubject, t.rawName];
    days.forEach(day => {
      periods.forEach(p => {
        if (shortDays.includes(day) && p === 6) { row.push('—'); return; }
        const cDay = cleanStr(day);
        row.push(slotMap[`${t.cleanName}_${t.cleanSubject}_${cDay}_${p}`] || slotMap[`${t.cleanName}_${cDay}_${p}`] || '');
      });
    });
    dataRows.push(row);
  });

  const totalRows = dataRows.length;
  const totalCols = 2 + (days.length * periods.length);
  
  outputSheet.getRange(1, 1, Math.max(totalRows, 100), Math.max(totalCols, 35)).breakApart();
  outputSheet.getRange(1, 1, totalRows, totalCols).setValues(dataRows).setHorizontalAlignment('center').setVerticalAlignment('middle');

  outputSheet.getRange(1, 1, 2, totalCols).setBackground('#1C4587').setFontColor('#FFFFFF').setFontWeight('bold');
  outputSheet.getRange(1, 1, 2, 1).merge();
  outputSheet.getRange(1, 2, 2, 1).merge();

  for (let dIdx = 0; dIdx < days.length; dIdx++) {
    outputSheet.getRange(1, 3 + (dIdx * 6), 1, 6).merge();
  }

  outputSheet.getRange(1, 1, totalRows, totalCols).setBorder(true, true, true, true, true, true, '#B7B7B7', SpreadsheetApp.BorderStyle.SOLID);
  outputSheet.setColumnWidth(1, 80); outputSheet.setColumnWidth(2, 120);
  for (let c = 3; c <= totalCols; c++) outputSheet.setColumnWidth(c, 45);

  outputSheet.setFrozenRows(2); outputSheet.setFrozenColumns(2);
}

function writeClassTimetableToSheet(ss, timetableData) {
  let outputSheet = ss.getSheetByName('クラス別時間割');
  if (!outputSheet) outputSheet = ss.insertSheet('クラス別時間割');
  else outputSheet.clear();

  const shortDays = getShortDays(ss);
  const days = ['月', '火', '水', '木', '金'];
  const periods = [1, 2, 3, 4, 5, 6];

  const configSheet = ss.getSheetByName('設定');
  let classList = [];
  if (configSheet && configSheet.getLastRow() > 1) {
    classList = configSheet.getRange(2, 1, configSheet.getLastRow() - 1, 1).getDisplayValues().map(r => String(r[0] || '').trim()).filter(Boolean);
  }

  if (classList.length === 0) return;

  const cleanStr = (s) => String(s || '').replace(/\s+/g, '').trim();
  const classSlotMap = {};

  (timetableData || []).forEach(item => {
    const cName = cleanStr(item['class'] || item.className);
    const day = cleanStr(item.day);
    const period = parseInt(item.period || 0, 10);
    const subject = cleanStr(item.subject);

    if (cName && day && period) {
      classSlotMap[`${cName}_${day}_${period}`] = subject;
    }
  });

  const row1 = ['クラス'];
  days.forEach(day => { row1.push(day); for (let i = 0; i < 5; i++) row1.push(''); });
  const row2 = [''];
  days.forEach(() => { periods.forEach(p => row2.push(p)); });

  const dataRows = [row1, row2];

  classList.forEach(cName => {
    const row = [cName];
    days.forEach(day => {
      periods.forEach(p => {
        if (shortDays.includes(day) && p === 6) { row.push('—'); return; }
        row.push(classSlotMap[`${cleanStr(cName)}_${cleanStr(day)}_${p}`] || '');
      });
    });
    dataRows.push(row);
  });

  const totalRows = dataRows.length;
  const totalCols = 1 + (days.length * periods.length);

  outputSheet.getRange(1, 1, Math.max(totalRows, 50), Math.max(totalCols, 35)).breakApart();
  outputSheet.getRange(1, 1, totalRows, totalCols).setValues(dataRows).setHorizontalAlignment('center').setVerticalAlignment('middle').setWrap(true);

  outputSheet.getRange(1, 1, 2, 1).merge();
  for (let dIdx = 0; dIdx < days.length; dIdx++) {
    outputSheet.getRange(1, 2 + (dIdx * 6), 1, 6).merge();
  }

  outputSheet.getRange(1, 1, totalRows, totalCols).setBorder(true, true, true, true, true, true, '#B7B7B7', SpreadsheetApp.BorderStyle.SOLID);
  outputSheet.setColumnWidth(1, 80);
  for (let c = 2; c <= totalCols; c++) outputSheet.setColumnWidth(c, 55);

  apply5SubjectColors(outputSheet, dataRows, totalRows, totalCols);
  outputSheet.setFrozenRows(2); outputSheet.setFrozenColumns(1);
}

function apply5SubjectColors(sheet, dataRows, totalRows, totalCols) {
  const colorMap = {
    '国語': '#FADBD8',
    '社会': '#FCF3CF',
    '数学': '#D4E6F1',
    '理科': '#D4EFDF',
    '英語': '#EBDEF0',
    '道徳': '#FFE6CC',
    '学活': '#FFF2CC',
    '総合': '#C5E0B4',
    '音楽': '#FFFFFF',
    '美術': '#FFFFFF',
    '保体': '#FFFFFF',
    '技術': '#FFFFFF',
    '家庭': '#FFFFFF'
  };

  const bgColors = [];
  for (let r = 0; r < totalRows; r++) {
    const rowBg = [];
    for (let c = 0; c < totalCols; c++) {
      if (r < 2) {
        rowBg.push('#2F5597');
      } else if (c === 0) {
        rowBg.push('#F2F2F2');
      } else {
        const val = String(dataRows[r][c] || '').trim();
        if (val === '—') rowBg.push('#E0E0E0');
        else if (colorMap[val] !== undefined) rowBg.push(colorMap[val]);
        else rowBg.push('#FFFFFF');
      }
    }
    bgColors.push(rowBg);
  }

  sheet.getRange(1, 1, totalRows, totalCols).setBackgrounds(bgColors);
  sheet.getRange(1, 1, 2, totalCols).setFontColor('#FFFFFF').setFontWeight('bold');
  sheet.getRange(3, 1, totalRows - 2, 1).setFontColor('#000000').setFontWeight('bold');
}

function checkTimetableErrors() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const ui = SpreadsheetApp.getUi();
  const classSheet = ss.getSheetByName('クラス別時間割');
  const teacherSheet = ss.getSheetByName('完成時間割');

  if (!classSheet || !teacherSheet) {
    ui.alert('エラー', '先に時間割を作成してください。', ui.ButtonSet.OK);
    return;
  }

  const days = ['月', '火', '水', '木', '金'];
  const errors = [];

  const tData = teacherSheet.getRange(1, 1, teacherSheet.getLastRow(), teacherSheet.getLastColumn()).getDisplayValues();

  for (let r = 2; r < tData.length; r++) {
    const teacherName = String(tData[r][1] || '').trim();
    if (!teacherName) continue;

    for (let d = 0; d < 5; d++) {
      let count = 0;
      for (let p = 0; p < 6; p++) {
        const colIdx = 2 + (d * 6) + p;
        const assignedClass = String(tData[r][colIdx] || '').trim();

        if (assignedClass && assignedClass !== '—') {
          count++;
        }
      }
      if (count > 5) {
        errors.push(`【教員コマ過密】${teacherName}: ${days[d]}曜日に 6コマ全埋まり`);
      }
    }
  }

  if (errors.length === 0) {
    ui.alert('✅ チェック完了', '時間割のエラーは見つかりませんでした！', ui.ButtonSet.OK);
  } else {
    const msg = `⚠️ ${errors.length} 件のエラー:\n\n` + errors.slice(0, 12).join('\n') + (errors.length > 12 ? '\n...他多数' : '');
    ui.alert('制約エラー検知', msg, ui.ButtonSet.OK);
  }
}

function autoFixErrorsSilent() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const classSheet = ss.getSheetByName('クラス別時間割');
  const configSheet = ss.getSheetByName('設定');

  if (!classSheet || !configSheet) return;

  const shortDays = getShortDays(ss);

  const lastRow = classSheet.getLastRow();
  const lastCol = classSheet.getLastColumn();
  const displayData = classSheet.getRange(1, 1, lastRow, lastCol).getDisplayValues();
  const days = ['月', '火', '水', '木', '金'];

  const subjectMaster = configSheet.getRange(2, 2, configSheet.getLastRow() - 1, 4).getValues();
  const getRequiredHours = (gradeNum) => {
    const req = {};
    subjectMaster.forEach(row => {
      const subjName = String(row[0] || '').trim();
      const hours = Number(row[gradeNum]) || 0;
      if (subjName && hours > 0) req[subjName] = hours;
    });
    return req;
  };

  let updateCount = 0;

  for (let r = 2; r < lastRow; r++) {
    const className = String(displayData[r][0] || '').trim();
    if (!className) continue;

    const gradeMatch = className.match(/^([1-3])/);
    if (!gradeMatch) continue;
    const gradeNum = Number(gradeMatch[1]);
    const requiredHours = getRequiredHours(gradeNum);

    const currentCounts = {};
    const emptySlots = [];

    for (let d = 0; d < 5; d++) {
      for (let p = 0; p < 6; p++) {
        const colIdx = 1 + (d * 6) + p;
        if (colIdx >= lastCol) break;
        if (shortDays.includes(days[d]) && p === 5) continue;

        const subj = String(displayData[r][colIdx] || '').trim();
        if (subj && subj !== '—') {
          currentCounts[subj] = (currentCounts[subj] || 0) + 1;
        } else if (subj === '') {
          emptySlots.push({ col: colIdx, day: days[d], period: p + 1 });
        }
      }
    }

    // ========== ステップ1: 不足している教科を優先的に補填 ==========
    for (const [subj, req] of Object.entries(requiredHours)) {
      const current = currentCounts[subj] || 0;
      let missing = req - current;

      while (missing > 0 && emptySlots.length > 0) {
        const slotIdx = Math.floor(Math.random() * emptySlots.length);
        const slot = emptySlots.splice(slotIdx, 1)[0];
        displayData[r][slot.col] = subj;
        updateCount++;
        missing--;
      }
    }

    // ========== ステップ2: 残った空欄をランダムに埋める ==========
    if (emptySlots.length > 0) {
      const subjectList = Object.keys(requiredHours);
      while (emptySlots.length > 0) {
        const randomSubj = subjectList[Math.floor(Math.random() * subjectList.length)];
        const slot = emptySlots.shift();
        displayData[r][slot.col] = randomSubj;
        updateCount++;
      }
    }
  }

  if (updateCount > 0) {
    classSheet.getRange(1, 1, lastRow, lastCol).setValues(displayData);
    apply5SubjectColors(classSheet, displayData, lastRow, lastCol);
  }
}

function autoFixErrors() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const classSheet = ss.getSheetByName('クラス別時間割');
  const configSheet = ss.getSheetByName('設定');

  if (!classSheet || !configSheet) return;

  const shortDays = getShortDays(ss);
  ss.toast('補填と調整を実行中...', '修復中', -1);

  const lastRow = classSheet.getLastRow();
  const lastCol = classSheet.getLastColumn();
  const displayData = classSheet.getRange(1, 1, lastRow, lastCol).getDisplayValues();
  const days = ['月', '火', '水', '木', '金'];

  const subjectMaster = configSheet.getRange(2, 2, configSheet.getLastRow() - 1, 4).getValues();
  const getRequiredHours = (gradeNum) => {
    const req = {};
    subjectMaster.forEach(row => {
      const subjName = String(row[0] || '').trim();
      const hours = Number(row[gradeNum]) || 0;
      if (subjName && hours > 0) req[subjName] = hours;
    });
    return req;
  };

  let updateCount = 0;

  for (let r = 2; r < lastRow; r++) {
    const className = String(displayData[r][0] || '').trim();
    if (!className) continue;

    const gradeMatch = className.match(/^([1-3])/);
    if (!gradeMatch) continue;
    const gradeNum = Number(gradeMatch[1]);
    const requiredHours = getRequiredHours(gradeNum);

    const currentCounts = {};
    const emptySlots = [];

    for (let d = 0; d < 5; d++) {
      for (let p = 0; p < 6; p++) {
        const colIdx = 1 + (d * 6) + p;
        if (colIdx >= lastCol) break;
        if (shortDays.includes(days[d]) && p === 5) continue;

        const subj = String(displayData[r][colIdx] || '').trim();
        if (subj && subj !== '—') {
          currentCounts[subj] = (currentCounts[subj] || 0) + 1;
        } else if (subj === '') {
          emptySlots.push({ col: colIdx, day: days[d], period: p + 1 });
        }
      }
    }

    // ========== ステップ1: 不足している教科を優先的に補填 ==========
    for (const [subj, req] of Object.entries(requiredHours)) {
      const current = currentCounts[subj] || 0;
      let missing = req - current;

      while (missing > 0 && emptySlots.length > 0) {
        const slotIdx = Math.floor(Math.random() * emptySlots.length);
        const slot = emptySlots.splice(slotIdx, 1)[0];
        displayData[r][slot.col] = subj;
        updateCount++;
        missing--;
      }
    }

    // ========== ステップ2: 残りの空欄を埋める（無い場合） ==========
    // もし まだ空欄が残っていて、全教科が揃っていない場合、
    // ランダムに教科を補填（実際には人が修正する想定）
    if (emptySlots.length > 0) {
      const subjectList = Object.keys(requiredHours);
      while (emptySlots.length > 0) {
        const randomSubj = subjectList[Math.floor(Math.random() * subjectList.length)];
        const slot = emptySlots.shift();
        displayData[r][slot.col] = randomSubj;
        updateCount++;
      }
    }
  }

  if (updateCount > 0) {
    classSheet.getRange(1, 1, lastRow, lastCol).setValues(displayData);
    apply5SubjectColors(classSheet, displayData, lastRow, lastCol);
    ss.toast(`自動修復完了: ${updateCount}コマ補填\n\n⚠️ ランダムに補填されています\n内容を確認して調整してください`, '完了', 5);
  } else {
    ss.toast('補填対象なし', 'チェック完了', 3);
  }
}
