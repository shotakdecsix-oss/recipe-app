/* ==========================================================================
   冷蔵庫レシピ — フロントエンド
   提案結果(scope 'r')と保存レシピ(scope 'k')を同じカード描画で扱う。
   カード内の状態（チャット・代替食材・タイマー・開閉）はすべてJS側に持ち、
   再描画のたびにDOMへ復元する。
   ========================================================================== */

// ---------------------------------------------------------------------------
// 共通ユーティリティ
// ---------------------------------------------------------------------------
function esc(s) {
  if (s === 0) return '0';
  if (!s) return '';
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// onclick="fn('...')" の引数に安全に埋め込む
function jsq(s) {
  return String(s == null ? '' : s)
    .replace(/\\/g, '\\\\').replace(/'/g, "\\'")
    .replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

function el(id) { return document.getElementById(id); }

function fmtTime(secs) {
  var m = Math.floor(secs / 60), s = secs % 60;
  return m > 0 ? (m + '分' + (s > 0 ? s + '秒' : '')) : s + '秒';
}

function fmtCountdown(secs) {
  var m = Math.floor(secs / 60), s = secs % 60;
  return (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
}

var toastTimer = null;
function showToast(msg) {
  var t = el('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { t.classList.remove('show'); }, 2400);
}

function showError(msg) {
  var e = el('error-box');
  e.textContent = '⚠ ' + msg;
  e.style.display = 'block';
}
function hideError() { el('error-box').style.display = 'none'; }

// ---------------------------------------------------------------------------
// スコープごとの状態
//   list: レシピ配列 / subsMap: 代替食材の辞書 / chats: 会話 / applied: 反映済み数
//   open: チャット開閉 / drafts: 入力途中 / subs: 選択した代替 / adds: 選択した追加食材
// ---------------------------------------------------------------------------
function newScope() {
  return { list: [], subsMap: [], chats: {}, applied: {}, open: {}, drafts: {}, subs: {}, adds: {} };
}
var S = { r: newScope(), k: newScope() };

var accState     = {};  // アコーディオン開閉 {"r:0:ing": true}
var activeTimers = {};  // {timerId: {endAt, remaining, paused, done, interval}}
var cardObserver = null;
var selectedIdx  = {};  // 提案カードの複数選択
var keptOpenId   = null;

// 入力条件
var tags = [];
var lastIngredients = [], lastMood = '', lastServings = 2;
var lastMaxTime = '', lastDrink = '', lastModel = '';
var shownTitles = [], lastGenAt = '', lastCandCount = 0;

var LS_HISTORY = 'recipe_history';
var LS_STATE   = 'recipe_state';
var HISTORY_WARN_AT = 50;

// ---------------------------------------------------------------------------
// 履歴（決定した献立）
// ---------------------------------------------------------------------------
function newId() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return Date.now() + '-' + Math.random().toString(36).slice(2, 10);
}

function loadHistory() {
  var list = [];
  try { list = JSON.parse(localStorage.getItem(LS_HISTORY)) || []; } catch (e) { return []; }
  if (!Array.isArray(list)) return [];
  var changed = false;
  list.forEach(function (r) {
    if (!r._id) { r._id = newId(); changed = true; }
    if (!r._keptAtISO && r._keptAt) {
      var d = new Date(String(r._keptAt).replace(/-/g, '/'));
      if (!isNaN(d.getTime())) { r._keptAtISO = d.toISOString(); changed = true; }
    }
  });
  if (changed) { try { localStorage.setItem(LS_HISTORY, JSON.stringify(list)); } catch (e) {} }
  return list;
}

var keptDishes = loadHistory();

function saveHistoryList() {
  try {
    localStorage.setItem(LS_HISTORY, JSON.stringify(keptDishes));
    return true;
  } catch (e) {
    console.error('[RecipeApp] history save failed:', e);
    showToast('⚠ 履歴を保存できませんでした（保存容量の上限に達した可能性があります）');
    return false;
  }
}

function updateHistCount() {
  var c = el('hist-count');
  if (c) c.textContent = keptDishes.length ? '（' + keptDishes.length + '）' : '';
  var t = el('hist-total');
  if (t) t.textContent = keptDishes.length ? ' — ' + keptDishes.length + '件' : '';
}

function makeKeptEntry(r) {
  var entry = JSON.parse(JSON.stringify(r));
  var now = new Date();
  entry._id        = newId();
  entry._keptAt    = now.toLocaleString('ja-JP');
  entry._keptAtISO = now.toISOString();
  entry._context = {
    ingredients: lastIngredients.slice(),
    mood: lastMood, servings: lastServings,
    max_time: lastMaxTime, drink: lastDrink
  };
  return entry;
}

function keepDish(i) {
  var r = S.r.list[i];
  if (!r) return;
  keptDishes.push(makeKeptEntry(r));
  if (!saveHistoryList()) { keptDishes.pop(); return; }
  updateHistCount();
  showToast(keptDishes.length >= HISTORY_WARN_AT
    ? '⚠ 履歴が' + keptDishes.length + '件です。古いものを整理してください'
    : '✅ 「' + r.title_ja + '」を決定！食材はそのまま次の献立を探せます');
  softReset();
}

function keepSelected() {
  var idxs = Object.keys(selectedIdx).filter(function (k) { return selectedIdx[k]; });
  if (!idxs.length) { showToast('レシピを選択してください'); return; }
  var added = 0;
  idxs.forEach(function (k) {
    var r = S.r.list[k];
    if (r) { keptDishes.push(makeKeptEntry(r)); added++; }
  });
  if (!saveHistoryList()) {
    for (var n = 0; n < added; n++) keptDishes.pop();
    return;
  }
  updateHistCount();
  showToast('✅ ' + added + '品を決定しました');
  softReset();
}

function removeKeptDish(id) {
  var r = keptDishes.find(function (x) { return String(x._id) === String(id); });
  if (!r) return;
  if (!window.confirm('「' + r.title_ja + '」を履歴から削除しますか？')) return;
  keptDishes = keptDishes.filter(function (x) { return String(x._id) !== String(id); });
  saveHistoryList();
  updateHistCount();
  renderHistoryGrid();
  if (keptOpenId === id) closeKeptModal();
}

// ---- 履歴パネル ----
var histFilter = null;
var histSelected = {};   // {_id: true} 履歴側の複数選択

function toggleHistSelect(id, cb) {
  if (cb.checked) histSelected[id] = true; else delete histSelected[id];
  var card = cb.parentNode;
  if (card) card.classList.toggle('selected', cb.checked);
  updateHistBar();
}

function clearHistSelection() {
  histSelected = {};
  renderHistoryGrid();
}

function selectedKeptDishes() {
  return keptDishes.filter(function (r) { return histSelected[r._id]; });
}

function updateHistBar() {
  var n = selectedKeptDishes().length;
  var bar = el('hist-bar');
  if (!bar) return;
  bar.classList.toggle('show', n > 0);
  el('hist-mb-count').textContent = n + '件選択中';
}

function shoppingFromHistory() {
  var list = selectedKeptDishes();
  if (!list.length) { showToast('献立を選択してください'); return; }
  var servings = (list[0]._context && list[0]._context.servings) || lastServings;
  openShoppingListFor(list, servings, []);
}

function timelineFromHistory() {
  var list = selectedKeptDishes();
  if (!list.length) { showToast('献立を選択してください'); return; }
  openTimelineFor(list);
}

function openHistory() {
  renderHistoryChips();
  renderHistoryGrid();
  openPanel('history-panel');
}
function closeHistory() { closePanel('history-panel'); }

function historyIngredients() {
  var counts = {};
  keptDishes.forEach(function (r) {
    ((r._context && r._context.ingredients) || []).forEach(function (ing) {
      counts[ing] = (counts[ing] || 0) + 1;
    });
  });
  return Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; }).slice(0, 12);
}

function renderHistoryChips() {
  var wrap = el('hist-chips');
  var ings = historyIngredients();
  if (!ings.length) { wrap.innerHTML = ''; return; }
  wrap.innerHTML = ings.map(function (ing) {
    return '<button class="hist-chip' + (histFilter === ing ? ' on' : '') + '" '
      + 'onclick="toggleHistFilter(\'' + jsq(ing) + '\')">' + esc(ing) + '</button>';
  }).join('');
}

function toggleHistFilter(ing) {
  histFilter = (histFilter === ing) ? null : ing;
  renderHistoryChips();
  renderHistoryGrid();
}

function filteredHistory() {
  var q = (el('hist-search') ? el('hist-search').value : '').trim().toLowerCase();
  var sort = el('hist-sort') ? el('hist-sort').value : 'new';

  var list = keptDishes.filter(function (r) {
    if (histFilter) {
      var ctxIngs = (r._context && r._context.ingredients) || [];
      if (ctxIngs.indexOf(histFilter) === -1) return false;
    }
    if (!q) return true;
    var hay = [r.title_ja, (r.ingredients_ja || []).join(' '),
      ((r._context && r._context.ingredients) || []).join(' ')].join(' ').toLowerCase();
    return hay.indexOf(q) !== -1;
  });

  var key = function (r) { return r._keptAtISO || ''; };
  if (sort === 'new')        list.sort(function (a, b) { return key(b) < key(a) ? -1 : 1; });
  else if (sort === 'old')   list.sort(function (a, b) { return key(a) < key(b) ? -1 : 1; });
  else if (sort === 'title') list.sort(function (a, b) { return String(a.title_ja).localeCompare(String(b.title_ja), 'ja'); });
  else if (sort === 'time')  list.sort(function (a, b) { return (a.cook_time_min || 999) - (b.cook_time_min || 999); });
  return list;
}

function renderHistoryGrid() {
  var grid = el('hist-grid');
  var list = filteredHistory();
  updateHistCount();
  if (!list.length) {
    grid.innerHTML = '<div class="panel-empty">'
      + (keptDishes.length ? '条件に合う献立がありません' : 'まだ決定した献立はありません')
      + '</div>';
    updateHistBar();
    return;
  }
  grid.innerHTML = list.map(function (r) {
    var date = r._keptAtISO ? new Date(r._keptAtISO).toLocaleDateString('ja-JP') : (r._keptAt || '');
    var ings = (r._context && r._context.ingredients || []).slice(0, 4).join('・');
    return '<div class="hist-card has-check' + (r._photo ? ' has-photo' : '')
      + (histSelected[r._id] ? ' selected' : '') + '" '
      + 'onclick="openKeptModal(\'' + jsq(r._id) + '\')">'
      + '<input type="checkbox" class="hist-check" ' + (histSelected[r._id] ? 'checked ' : '')
      + 'onclick="event.stopPropagation()" onchange="toggleHistSelect(\'' + jsq(r._id) + '\',this)" '
      + 'aria-label="この献立を選択">'
      + '<button class="hist-del" onclick="event.stopPropagation();removeKeptDish(\'' + jsq(r._id) + '\')" aria-label="削除">✕</button>'
      + (r._photo ? '<div class="photo-wrap hist-photo-wrap">' + photoHtml(r._photo, 'hist-photo') + '</div>' : '')
      + '<div class="hist-card-title">' + esc(r.title_ja) + '</div>'
      + '<div class="hist-card-meta">'
      + '<span>' + esc(date) + '</span>'
      + (r.cook_time_min ? '<span>⏱ ' + r.cook_time_min + '分</span>' : '')
      + (r.difficulty ? '<span>' + esc(r.difficulty) + '</span>' : '')
      + ((r._chat && r._chat.length) ? '<span>💬 ' + Math.ceil(r._chat.length / 2) + '</span>' : '')
      + '</div>'
      + (ings ? '<div class="hist-card-ings">🥕 ' + esc(ings) + '</div>' : '')
      + '</div>';
  }).join('');
  updateHistBar();
}

var histWasOpen = false;

function openKeptModal(id) {
  var dish = keptDishes.find(function (x) { return String(x._id) === String(id); });
  if (!dish) return;
  keptOpenId = id;
  histWasOpen = el('history-panel').classList.contains('open');
  if (histWasOpen) el('history-panel').classList.remove('open');
  S.k = newScope();
  S.k.list = [dish];
  S.k.chats[0]   = (dish._chat || []).slice();
  S.k.applied[0] = dish._chatApplied || 0;
  renderKeptDetail();
  openPanel('kept-modal');
}

function renderKeptDetail() {
  var dish = S.k.list[0];
  if (!dish) return;
  var servings = (dish._context && dish._context.servings) || 2;
  snapshotAccStates(); snapshotChatDrafts('k');
  var ctx = dish._context || {};
  var reuseBtn = (ctx.ingredients && ctx.ingredients.length)
    ? '<button class="btn-reuse" onclick="reuseContext()">🔄 この条件でもう一度提案する'
      + '<br><span style="font-size:.74rem;color:var(--muted)">🥕 ' + esc(ctx.ingredients.join('・'))
      + (ctx.mood ? ' / ' + esc(ctx.mood) : '') + '</span></button>'
    : '';
  el('kept-modal-body').innerHTML =
    '<div class="hint" style="margin-bottom:10px">決定日時: ' + esc(dish._keptAt || '') + '</div>'
    + reuseBtn
    + renderCard('k', 0, dish, { servings: servings, showKeep: false, showCheck: false });
  restoreAccStates(); restoreChats('k'); restoreTimers();
  loadPhotos('k');
}

function closeKeptModal() {
  keptOpenId = null;
  closePanel('kept-modal');
  if (histWasOpen) {          // 履歴から開いた場合は履歴に戻る
    histWasOpen = false;
    renderHistoryChips();
    renderHistoryGrid();
    openPanel('history-panel');
  }
}

// 保存時の条件を入力欄に戻して、そのまま再提案する
function reuseContext() {
  var dish = S.k.list[0];
  var ctx = dish && dish._context;
  if (!ctx || !ctx.ingredients || !ctx.ingredients.length) return;

  tags = ctx.ingredients.slice();
  lastMood = ctx.mood || '';
  lastServings = ctx.servings || 2;
  lastMaxTime = ctx.max_time || '';
  lastDrink = ctx.drink || '';
  el('mood').value = lastMood;
  el('servings').value = lastServings;
  el('max-time').value = lastMaxTime;
  el('drink').value = lastDrink;
  renderTags();

  histWasOpen = false;            // 履歴には戻らずに提案画面へ
  closeKeptModal();
  closePanel('history-panel');
  suggest();
}

// 保存レシピへの変更を localStorage に書き戻す
function persistKeptDish() {
  var dish = S.k.list[0];
  if (!dish) return;
  dish._chat = (S.k.chats[0] || []).slice();
  dish._chatApplied = S.k.applied[0] || 0;
  dish._updatedAtISO = new Date().toISOString();
  var i = keptDishes.findIndex(function (x) { return String(x._id) === String(dish._id); });
  if (i !== -1) { keptDishes[i] = dish; saveHistoryList(); }
}

// ---- エクスポート / インポート ----
function recipeToText(r) {
  var lines = ['【' + r.title_ja + '】'];
  if (r.cook_time_min) lines.push('調理時間: ' + r.cook_time_min + '分');
  if (r.difficulty) lines.push('難易度: ' + r.difficulty);
  if ((r.ingredients_ja || []).length) {
    lines.push('', '■ 材料');
    r.ingredients_ja.forEach(function (x) { lines.push('・' + x); });
  }
  if ((r.steps_ja || []).length) {
    lines.push('', '■ 作り方');
    r.steps_ja.forEach(function (s, i) { lines.push((i + 1) + '. ' + s); });
  }
  if ((r.tips || []).length) {
    lines.push('', '■ コツ・ポイント');
    r.tips.forEach(function (t) { lines.push('・' + t); });
  }
  if (r.pairing) lines.push('', '■ お酒のペアリング', r.pairing.drink + ': ' + r.pairing.reason);
  return lines.join('\n');
}

function exportHistory(kind) {
  if (!keptDishes.length) { showToast('履歴がありません'); return; }
  if (kind === 'json') {
    var blob = new Blob([JSON.stringify(keptDishes, null, 2)], { type: 'application/json' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'recipe-history-' + new Date().toISOString().slice(0, 10) + '.json';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    showToast('⬇ JSONを書き出しました');
    return;
  }
  copyText(filteredHistory().map(recipeToText).join('\n\n────────\n\n'),
    function () { showToast('📋 履歴をコピーしました'); });
}

function importHistoryPrompt() {
  var input = document.createElement('input');
  input.type = 'file'; input.accept = 'application/json,.json';
  input.onchange = function () {
    var f = input.files && input.files[0];
    if (!f) return;
    var reader = new FileReader();
    reader.onload = function () {
      try {
        var incoming = JSON.parse(reader.result);
        if (!Array.isArray(incoming)) throw new Error('形式が違います');
        var existing = {};
        keptDishes.forEach(function (r) { existing[r._id] = true; });
        var added = 0;
        incoming.forEach(function (r) {
          if (r && r.title_ja && !existing[r._id]) {
            if (!r._id) r._id = newId();
            keptDishes.push(r); added++;
          }
        });
        saveHistoryList(); updateHistCount(); renderHistoryChips(); renderHistoryGrid();
        showToast('⬆ ' + added + '件を追加しました');
      } catch (e) {
        showToast('⚠ 読み込めませんでした: ' + e.message);
      }
    };
    reader.readAsText(f);
  };
  input.click();
}

// ---------------------------------------------------------------------------
// パネル / モーダル共通（Escキー・背景クリックで閉じる）
// ---------------------------------------------------------------------------
var focusStack = [];

function focusablesIn(root) {
  return Array.prototype.slice.call(root.querySelectorAll(
    'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),'
    + 'textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'
  )).filter(function (e) { return e.offsetParent !== null; });
}

// 開いているオーバーレイのうち最前面（z-indexが最大）のものを返す
function topOverlay() {
  var open = Array.prototype.slice.call(
    document.querySelectorAll('.panel-overlay.open, .modal-overlay.open'));
  if (!open.length) return null;
  return open.reduce(function (best, e) {
    var z = parseInt(getComputedStyle(e).zIndex, 10) || 0;
    var bz = parseInt(getComputedStyle(best).zIndex, 10) || 0;
    return z >= bz ? e : best;
  });
}

function openPanel(id) {
  var node = el(id);
  var wasOpen = node.classList.contains('open');
  node.classList.add('open');
  document.body.style.overflow = 'hidden';
  if (!wasOpen) {
    focusStack.push(document.activeElement);
    setTimeout(function () {
      var f = focusablesIn(node);
      if (f.length) { try { f[0].focus(); } catch (e) {} }
    }, 0);
  }
}

function closePanel(id) {
  var node = el(id);
  var wasOpen = node.classList.contains('open');
  node.classList.remove('open');
  if (!document.querySelector('.panel-overlay.open, .modal-overlay.open')) {
    document.body.style.overflow = '';
  }
  if (wasOpen) {
    var prev = focusStack.pop();
    if (prev && prev.focus) { try { prev.focus(); } catch (e) {} }
  }
}

function closeTopOverlay() {
  var top = topOverlay();
  if (!top) return false;
  if (top.id === 'kept-modal') closeKeptModal();
  else if (top.id === 'sub-modal') closeSubModal();
  else closePanel(top.id);
  return true;
}

// モーダルの外へフォーカスが出ないようにする
document.addEventListener('keydown', function (e) {
  if (e.key !== 'Tab') return;
  var top = topOverlay();
  if (!top) return;
  var f = focusablesIn(top);
  if (!f.length) return;
  var first = f[0], last = f[f.length - 1];
  if (!top.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') closeTopOverlay();
});
document.addEventListener('click', function (e) {
  if (e.target.classList && e.target.classList.contains('panel-overlay')) closePanel(e.target.id);
});

// ---------------------------------------------------------------------------
// 食材タグ入力
// ---------------------------------------------------------------------------
var suppressBlurAdd = false;

function addTag(val) {
  if (!val || tags.indexOf(val) !== -1) return;
  tags.push(val);
  renderTags();
}
function removeTag(val) {
  tags = tags.filter(function (t) { return t !== val; });
  renderTags();
}
function renderTags() {
  var wrap = el('tag-wrap'), input = el('ingredient-input');
  Array.prototype.forEach.call(wrap.querySelectorAll('.tag'), function (e) { e.remove(); });
  tags.forEach(function (t) {
    var d = document.createElement('div');
    d.className = 'tag';
    d.innerHTML = esc(t) + '<span class="tag-del" onclick="removeTag(\'' + jsq(t) + '\')">✕</span>';
    wrap.insertBefore(d, input);
  });
}

function setupTagInput() {
  var input = el('ingredient-input'), wrap = el('tag-wrap');
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      addTag(this.value.replace(/,/g, '').trim());
      this.value = '';
    }
  });
  // ✕ をタップしたときのblurで意図しないタグが増えるのを防ぐ
  function guard(e) { if (e.target.classList.contains('tag-del')) suppressBlurAdd = true; }
  wrap.addEventListener('mousedown', guard);
  wrap.addEventListener('touchstart', guard, { passive: true });
  input.addEventListener('blur', function () {
    if (suppressBlurAdd) { suppressBlurAdd = false; return; }
    var v = this.value.replace(/,/g, '').trim();
    if (v) { addTag(v); this.value = ''; }
  });
  wrap.addEventListener('click', function (e) {
    if (e.target === this || e.target.id === 'ingredient-input') input.focus();
  });
}

// ---------------------------------------------------------------------------
// 通信
// ---------------------------------------------------------------------------
function postJSON(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }).then(function (r) {
    return r.json().catch(function () { throw new Error('サーバー応答が不正です (' + r.status + ')'); });
  });
}

var loadingMsgs = ['✨ 食材の組み合わせを考えています…', '📝 レシピを書いています…', '🍷 ペアリングを選んでいます…'];
var msgIdx = 0, msgTimer = null;

function setLoading(on) {
  el('loading').style.display = on ? 'block' : 'none';
  el('btn-suggest').disabled = on;
  var rb = el('btn-retry');
  if (rb) rb.disabled = on;
  if (on) {
    msgIdx = 0;
    el('loading-msg').textContent = loadingMsgs[0];
    msgTimer = setInterval(function () {
      msgIdx = (msgIdx + 1) % loadingMsgs.length;
      el('loading-msg').textContent = loadingMsgs[msgIdx];
    }, 4000);
  } else {
    clearInterval(msgTimer);
  }
}

var POLL_MAX = 90;   // 2秒 × 90 = 最長3分でタイムアウト

function pollJob(jobId, onSuccess, attempt) {
  attempt = attempt || 0;
  if (attempt >= POLL_MAX) {
    setLoading(false);
    showError('時間内に応答がありませんでした。もう一度お試しください');
    return;
  }
  fetch('/api/job/' + jobId)
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data.status === 'pending') {
        setTimeout(function () { pollJob(jobId, onSuccess, attempt + 1); }, 2000);
      } else if (data.status === 'done') {
        setLoading(false); onSuccess(data);
      } else {
        setLoading(false);
        showError(data.error || 'エラーが発生しました');
      }
    })
    .catch(function (e) { setLoading(false); showError('通信エラー: ' + e.message); });
}

function fetchRecipes(body, onSuccess) {
  postJSON('/api/recipe', body)
    .then(function (data) {
      if (data.error) { setLoading(false); showError(data.error); return; }
      if (data.job_id) { pollJob(data.job_id, onSuccess, 0); return; }
      setLoading(false); onSuccess(data);
    })
    .catch(function (e) { setLoading(false); showError('通信エラー: ' + e.message); });
}

function currentParams() {
  return {
    ingredients: lastIngredients, mood: lastMood, servings: lastServings,
    max_time: lastMaxTime, drink: lastDrink, model: lastModel
  };
}

function readForm() {
  lastIngredients = tags.slice();
  lastMood     = el('mood').value.trim();
  lastServings = parseInt(el('servings').value, 10) || 2;
  lastMaxTime  = el('max-time').value;
  lastDrink    = el('drink').value.trim();
  lastModel    = el('model').value || '';
}

function suggest() {
  if (!tags.length) { showError('食材を1つ以上入力してください'); return; }
  hideError();
  readForm();
  shownTitles = [];
  setLoading(true);
  el('results').innerHTML = '';
  var body = currentParams();
  body.exclude_titles = keptDishes.map(function (r) { return r.title_ja; });
  fetchRecipes(body, function (data) {
    shownTitles = data.recipes.map(function (r) { return r.title_ja; });
    renderResults(data);
  });
}

function retry() {
  hideError();
  setLoading(true);
  var body = currentParams();
  body.exclude_titles = shownTitles.concat(keptDishes.map(function (r) { return r.title_ja; }));
  fetchRecipes(body, function (data) {
    shownTitles = shownTitles.concat(data.recipes.map(function (r) { return r.title_ja; }));
    renderResults(data);
  });
}

// ---------------------------------------------------------------------------
// 料理のイメージ写真（Pexels）
//   レシピはAI生成なので実物の写真は存在しない。ジャンルの近い写真を当てるだけ。
//   誤解を避けるため必ず「イメージ」と明示し、撮影者クレジットを出す。
// ---------------------------------------------------------------------------
var photosEnabled = true;    // /api/version の photos_enabled で上書き
var photoPending  = {};

function photoQueryOf(r) { return String(r.image_query || r.title_ja || '').trim(); }

function photoHtml(p, cls) {
  var big = (cls === 'recipe-photo');
  var src = big ? (p.url || p.thumb) : (p.thumb || p.url);
  if (!src) return '';
  // サムネイル側のクレジットはリンクにしない。
  // カード中央がリンクと重なると、カード自体のタップを奪ってしまうため。
  var credit = '';
  if (p.credit) {
    credit = big
      ? '<a class="photo-credit" href="' + esc(p.credit_url || p.page) + '" target="_blank" '
        + 'rel="noopener" onclick="event.stopPropagation()">📷 ' + esc(p.credit) + ' / Pexels</a>'
      : '<span class="photo-credit">📷 ' + esc(p.credit) + ' / Pexels</span>';
  }
  return '<img class="' + cls + '" src="' + esc(src) + '" alt="" loading="lazy">'
    + '<span class="photo-note">イメージ</span>' + credit;
}

function applyPhoto(sc, i, p) {
  var box = el(sc + '-photo-' + i);
  if (box) box.innerHTML = photoHtml(p, 'recipe-photo');
  if (sc === 'r') {
    var sum = el('sum-photo-' + i);
    if (sum) sum.innerHTML = photoHtml(p, 'sum-photo');
  }
}

// 表示中のレシピに写真が無ければ非同期で取りに行く。失敗しても画面は壊さない
function loadPhotos(sc) {
  if (!photosEnabled) return;
  S[sc].list.forEach(function (r, i) {
    if (r._photo) { applyPhoto(sc, i, r._photo); return; }
    if (r._photo === null) return;          // 検索済みで見つからなかった
    var q = photoQueryOf(r);
    var pkey = sc + ':' + i;
    if (!q || photoPending[pkey]) return;
    photoPending[pkey] = true;
    fetch('/api/photo?q=' + encodeURIComponent(q))
      .then(function (res) { return res.json(); })
      .then(function (d) {
        delete photoPending[pkey];
        if (d.reason === 'no_key') { photosEnabled = false; return; }
        r._photo = d.photo || null;
        if (!r._photo) return;
        applyPhoto(sc, i, r._photo);
        if (sc === 'k') persistKeptDish(); else saveState();
      })
      .catch(function () { delete photoPending[pkey]; });
  });
}

// ---------------------------------------------------------------------------
// カード描画（提案結果・保存レシピ共通）
// ---------------------------------------------------------------------------
function diffClass(d) { return d === '難しい' ? 'badge-diff hard' : 'badge-diff'; }

// 材料名から代替食材の定義を引く。部分一致の誤爆を避けるため最長一致を採用
function findSubKey(subs, ing) {
  var keys = Object.keys(subs).filter(function (k) {
    return k.length >= 2 && ing.indexOf(k) !== -1;
  });
  keys.sort(function (a, b) { return b.length - a.length; });
  return keys[0];
}

function renderCard(sc, i, r, opts) {
  opts = opts || {};
  var st = S[sc];
  var servings = opts.servings || lastServings;
  var P = sc + '-';

  var steps = (r.steps_ja || []).map(function (s, si) {
    var secs = parseStepTime(s);
    var tid = 'timer-' + sc + '-' + i + '-' + si;
    var timerHtml = secs
      ? '<span class="timer-display" id="disp-' + tid + '"></span>'
        + '<button class="timer-btn" id="btn-' + tid + '" onclick="toggleTimer(\'' + tid + '\',' + secs + ')">⏱ ' + fmtTime(secs) + '</button>'
      : '';
    return '<li style="align-items:center"><span class="step-num">' + (si + 1) + '</span>'
      + '<span style="flex:1">' + esc(s) + '</span>' + timerHtml + '</li>';
  }).join('');

  var subs = {};
  (r.substitutions || []).forEach(function (s) { if (s.ingredient_name) subs[s.ingredient_name] = s; });
  st.subsMap[i] = subs;

  var ings = (r.ingredients_ja || []).map(function (ing) {
    var subKey = findSubKey(subs, ing);
    var sub = subKey ? subs[subKey] : null;
    var selSub = st.subs[i] && st.subs[i][subKey];
    if (!sub) return '<span class="ing-chip">' + esc(ing) + '</span>';
    var chipClass = 'chip-sub' + (selSub ? ' chip-sub--selected' : '');
    var label = selSub === '省略'
      ? '<span class="chip-sub-orig">' + esc(ing) + '</span> <span class="chip-sub-note">省略</span>'
      : selSub
        ? '<span class="chip-sub-orig">' + esc(ing) + '</span><span class="chip-sub-note"> → ' + esc(selSub) + '</span>'
        : esc(ing) + ' <span class="chip-sub-note">▼変更</span>';
    return '<button class="' + chipClass + '" onclick="openSubModal(\'' + sc + '\',' + i + ',\'' + jsq(subKey) + '\')">'
      + label + '</button>';
  }).join('');

  var additionsHtml = (r.suggested_additions && r.suggested_additions.length)
    ? '<details class="acc" data-acc="' + sc + ':' + i + ':add"><summary>➕ 追加するともっと美味しくなる食材<span class="arrow">▾</span></summary>'
      + '<div class="d-body" style="display:flex;flex-wrap:wrap;gap:8px">'
      + r.suggested_additions.map(function (a) {
          var on = (st.adds[i] || []).indexOf(a.name_ja) !== -1;
          return '<button class="chip-add' + (on ? ' chip-add--selected' : '') + '" '
            + 'onclick="toggleAddition(\'' + sc + '\',' + i + ',\'' + jsq(a.name_ja) + '\',this)" '
            + 'title="' + esc(a.reason) + '">＋ ' + esc(a.name_ja)
            + '<span class="chip-add-reason">' + esc(a.reason) + '</span></button>';
        }).join('')
      + '</div></details>'
    : '';

  var tipsHtml = (r.tips && r.tips.length)
    ? '<details class="acc" data-acc="' + sc + ':' + i + ':tips"><summary>✦ コツ・ポイント<span class="arrow">▾</span></summary>'
      + '<div class="d-body"><ul style="list-style:none;display:flex;flex-direction:column;gap:6px">'
      + r.tips.map(function (t) {
          return '<li style="font-size:.84rem;display:flex;gap:8px;line-height:1.5">'
            + '<span style="color:var(--gold);flex-shrink:0">✦</span><span>' + esc(t) + '</span></li>';
        }).join('')
      + '</ul></div></details>'
    : '';

  var pairingHtml = r.pairing
    ? '<div class="pairing-box"><div class="pairing-icon">🍷</div>'
      + '<div><div class="pairing-drink">' + esc(r.pairing.drink) + '</div>'
      + '<div class="pairing-reason">' + esc(r.pairing.reason) + '</div></div></div>'
    : '';

  var hasChanges = (st.subs[i] && Object.keys(st.subs[i]).length > 0)
    || (st.adds[i] && st.adds[i].length > 0);

  return '<div class="recipe-card' + (selectedIdx[i] && sc === 'r' ? ' selected' : '') + '" '
    + 'id="' + P + 'recipe-card-' + i + '" data-idx="' + i + '">'
    + '<div class="photo-wrap" id="' + P + 'photo-' + i + '">'
    + (r._photo ? photoHtml(r._photo, 'recipe-photo') : '') + '</div>'
    + '<div class="recipe-header" style="position:relative">'
    + (opts.showCheck
        ? '<input type="checkbox" class="card-check" id="' + P + 'check-' + i + '" '
          + (selectedIdx[i] ? 'checked ' : '')
          + 'onchange="toggleCardSelect(' + i + ',this)" aria-label="このレシピを選択">'
        : '')
    + '<div class="recipe-num"' + (opts.showCheck ? ' style="margin-left:30px"' : '') + '>'
    + (sc === 'k' ? '保存済み' : 'レシピ ' + (i + 1)) + '</div>'
    + '<div class="recipe-title">' + esc(r.title_ja) + '</div>'
    + '<div class="recipe-badges">'
    + (r.cook_time_min ? '<span class="badge badge-time">⏱ ' + r.cook_time_min + '分</span>' : '')
    + (r.difficulty ? '<span class="badge ' + diffClass(r.difficulty) + '">' + esc(r.difficulty) + '</span>' : '')
    + '</div>'
    + '<button class="btn-copy" id="' + P + 'copy-btn-' + i + '" onclick="copyRecipe(\'' + sc + '\',' + i + ')">📋 コピー</button>'
    + '</div>'
    + '<div class="recipe-body">'
    + (r.reason ? '<div class="reason-box">' + esc(r.reason) + '</div>' : '')
    + (ings ? '<details class="acc" data-acc="' + sc + ':' + i + ':ing" open><summary>🥕 材料（' + servings + '人前）<span class="arrow">▾</span></summary><div class="d-body ingredients-grid">' + ings + '</div></details>' : '')
    + (steps ? '<details class="acc" data-acc="' + sc + ':' + i + ':steps" open><summary>👨‍🍳 作り方<span class="arrow">▾</span></summary><div class="d-body"><ul class="steps-list">' + steps + '</ul></div></details>' : '')
    + '<div id="' + P + 'rewrite-bar-' + i + '" class="rewrite-bar-wrap" style="display:' + (hasChanges ? 'block' : 'none') + '">'
    + '<button class="btn-rewrite" onclick="applyRewrite(\'' + sc + '\',' + i + ')">✨ この変更でレシピを更新</button></div>'
    + additionsHtml + tipsHtml + pairingHtml
    + '</div>'
    + '<div class="chat-section">'
    + '<button class="chat-toggle" onclick="toggleChat(\'' + sc + '\',' + i + ')">💬 このレシピについて質問する</button>'
    + '<div class="chat-body" id="' + P + 'chat-body-' + i + '">'
    + '<div class="chat-messages" id="' + P + 'chat-msgs-' + i + '"></div>'
    + '<div class="chat-input-row">'
    + '<input class="chat-input" id="' + P + 'chat-input-' + i + '" type="text" placeholder="例: 代替食材は？ アレンジは？" '
    + 'onkeydown="if(event.key===\'Enter\')sendChat(\'' + sc + '\',' + i + ')">'
    + '<button class="chat-send" id="' + P + 'chat-send-' + i + '" onclick="sendChat(\'' + sc + '\',' + i + ')">送信</button>'
    + '</div>'
    + '<div id="' + P + 'chat-rewrite-bar-' + i + '" style="display:none;margin-top:10px">'
    + '<button class="btn-rewrite btn-rewrite--chat" onclick="applyRewriteFromChat(\'' + sc + '\',' + i + ')">💬 この会話を踏まえてレシピを更新</button>'
    + '</div></div></div>'
    + (opts.showKeep
        ? '<div class="keep-bar"><button class="btn-keep" onclick="keepDish(' + i + ')">✅ これに決めた（次の献立へ）</button></div>'
        : '')
    + '</div>';
}

// ---------------------------------------------------------------------------
// 提案結果の描画
// ---------------------------------------------------------------------------
// 「鶏もも肉 300g」→「鶏もも肉」
function stripAmount(s) {
  return String(s)
    .replace(/[\s　]+[^\s　]*[0-9０-９].*$/, '')
    .replace(/[\s　]+(少々|適量|お好みで).*$/, '')
    .trim();
}
var SEASONINGS = ['塩', 'こしょう', '胡椒', '醤油', 'しょうゆ', '味噌', 'みそ', '砂糖', '酒', 'みりん', '酢',
  '油', 'オイル', 'バター', 'マヨネーズ', 'ケチャップ', 'ソース', 'だし', '出汁', 'コンソメ', '片栗粉',
  '小麦粉', '水', '料理酒', 'ごま', '七味', '一味'];
function isSeasoning(nm) {
  return SEASONINGS.some(function (s) { return nm.indexOf(s) !== -1; });
}

function renderResults(data) {
  if (data._fresh !== false) {
    S.r = newScope();
    selectedIdx = {};
    accState = {};
    Object.keys(activeTimers).forEach(function (id) {
      if (activeTimers[id] && activeTimers[id].interval) clearInterval(activeTimers[id].interval);
    });
    activeTimers = {};
  }
  if (data.recipes) S.r.list = data.recipes;
  if (data.generated_at) lastGenAt = data.generated_at;
  if (data.candidate_count) lastCandCount = data.candidate_count;

  // 再描画では interval を止めるだけ。タイマーの状態（終了時刻）は保持する
  Object.keys(activeTimers).forEach(function (id) {
    if (activeTimers[id] && activeTimers[id].interval) clearInterval(activeTimers[id].interval);
    if (activeTimers[id]) activeTimers[id].interval = null;
  });

  var recipes = S.r.list;
  var meta = '<div class="results-meta">'
    + '<span>' + (lastCandCount || recipes.length) + '件のレシピから選出 · ' + esc(lastGenAt) + '</span>'
    + '<button class="btn-retry" id="btn-retry" onclick="retry()">🔄 別の提案を見る</button></div>';

  var summaryHtml = '';
  if (recipes.length) {
    var sumCards = recipes.map(function (r, i) {
      // 比較できるよう主要食材と「買い足しが要る食材」を出す
      var main = (r.ingredients_ja || []).slice(0, 3).map(stripAmount).filter(Boolean).join('・');
      var missing = (r.ingredients_ja || []).map(stripAmount).filter(function (nm) {
        if (!nm || isSeasoning(nm)) return false;
        return !lastIngredients.some(function (h) {
          return h && (nm.indexOf(h) !== -1 || h.indexOf(nm) !== -1);
        });
      }).slice(0, 3);
      return '<div class="sum-card" id="sum-card-' + i + '" onclick="scrollToRecipe(' + i + ')">'
        + '<div class="photo-wrap sum-photo-wrap" id="sum-photo-' + i + '">'
        + (r._photo ? photoHtml(r._photo, 'sum-photo') : '') + '</div>'
        + '<div class="sum-num">レシピ ' + (i + 1) + '</div>'
        + '<div class="sum-title">' + esc(r.title_ja) + '</div>'
        + '<div class="sum-meta">'
        + (r.cook_time_min ? '<span class="badge badge-time">⏱ ' + r.cook_time_min + '分</span>' : '')
        + (r.difficulty ? '<span class="badge ' + diffClass(r.difficulty) + '">' + esc(r.difficulty) + '</span>' : '')
        + '</div>'
        + (main ? '<div class="sum-ings">🥕 ' + esc(main) + '</div>' : '')
        + (missing.length ? '<div class="sum-missing">🛒 買い足し: ' + esc(missing.join('・')) + '</div>' : '')
        + (r.reason ? '<div class="sum-reason">' + esc(r.reason) + '</div>' : '')
        + (r.pairing ? '<div class="sum-pair">🍷 ' + esc(r.pairing.drink) + '</div>' : '')
        + '</div>';
    }).join('');
    summaryHtml = '<div class="sum-label">📊 ' + recipes.length + 'つの提案をくらべる（タップで詳細へ）</div>'
      + '<div class="summary-row" id="summary-row">' + sumCards + '</div>';
  }

  var cards = recipes.map(function (r, i) {
    return renderCard('r', i, r, { servings: lastServings, showKeep: true, showCheck: true });
  }).join('');

  snapshotAccStates();
  snapshotChatDrafts('r');
  el('results').innerHTML = meta + summaryHtml + cards;
  restoreAccStates();
  restoreChats('r');
  restoreTimers();
  loadPhotos('r');
  updateMultiBar();
  saveState();
  setupCardObserver();
}

// ---------------------------------------------------------------------------
// 状態のスナップショット / 復元
// ---------------------------------------------------------------------------
function snapshotAccStates() {
  document.querySelectorAll('details[data-acc]').forEach(function (d) {
    accState[d.getAttribute('data-acc')] = d.open;
  });
}
function restoreAccStates() {
  document.querySelectorAll('details[data-acc]').forEach(function (d) {
    var k = d.getAttribute('data-acc');
    if (Object.prototype.hasOwnProperty.call(accState, k)) d.open = accState[k];
  });
}
function snapshotChatDrafts(sc) {
  var st = S[sc];
  st.list.forEach(function (_, i) {
    var e = el(sc + '-chat-input-' + i);
    if (e) st.drafts[i] = e.value;
  });
}
function appendMsg(sc, i, type, text) {
  var msgs = el(sc + '-chat-msgs-' + i);
  if (!msgs) return null;
  var d = document.createElement('div');
  d.className = 'msg msg-' + type;
  d.textContent = text;
  msgs.appendChild(d);
  msgs.scrollTop = msgs.scrollHeight;
  return d;
}
function appendDivider(sc, i) {
  var msgs = el(sc + '-chat-msgs-' + i);
  if (!msgs) return;
  var d = document.createElement('div');
  d.className = 'chat-divider';
  d.textContent = '— ここまでレシピに反映済み —';
  msgs.appendChild(d);
}
function restoreChats(sc) {
  var st = S[sc];
  st.list.forEach(function (_, i) {
    var hist = st.chats[i] || [];
    var msgs = el(sc + '-chat-msgs-' + i);
    if (msgs) {
      msgs.innerHTML = '';
      hist.forEach(function (m, mi) {
        appendMsg(sc, i, m.role === 'user' ? 'user' : 'ai', m.content);
        if ((st.applied[i] || 0) === mi + 1) appendDivider(sc, i);
      });
    }
    var input = el(sc + '-chat-input-' + i);
    if (input && st.drafts[i]) input.value = st.drafts[i];
    if (hist.length && st.open[i] === undefined) st.open[i] = true;
    var body = el(sc + '-chat-body-' + i);
    if (body && st.open[i]) body.classList.add('open');
    var bar = el(sc + '-chat-rewrite-bar-' + i);
    if (bar) bar.style.display = (hist.length > (st.applied[i] || 0)) ? 'block' : 'none';
  });
}

// ---------------------------------------------------------------------------
// タイマー（終了時刻ベース。バックグラウンドでもズレない）
// ---------------------------------------------------------------------------
function parseStepTime(text) {
  // 「3分の1」「分量」など時間でない表現を除外する
  var cleaned = String(text).replace(/\d+分の\d+/g, '').replace(/分量/g, '');
  var m = cleaned.match(/(\d+)分(\d+)秒/);
  if (m) return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
  m = cleaned.match(/(\d+)[〜～\-](\d+)\s*分/);
  if (m) { var mx = parseInt(m[2], 10); if (mx > 0 && mx <= 180) return mx * 60; }
  m = cleaned.match(/(\d+)\s*分/);
  if (m) { var mins = parseInt(m[1], 10); if (mins > 0 && mins <= 180) return mins * 60; }
  m = cleaned.match(/(\d+)\s*秒/);
  if (m) { var s = parseInt(m[1], 10); if (s > 0 && s <= 600) return s; }
  return null;
}

function remainingOf(st) {
  if (!st) return 0;
  if (st.paused || !st.endAt) return Math.max(0, st.remaining || 0);
  return Math.max(0, Math.round((st.endAt - Date.now()) / 1000));
}

function toggleTimer(tid, totalSecs) {
  var dispEl = el('disp-' + tid), btnEl = el('btn-' + tid);
  if (!dispEl || !btnEl) return;
  var st = activeTimers[tid];
  if (st && !st.done) {
    if (st.paused) {
      st.paused = false;
      st.endAt = Date.now() + (st.remaining || 0) * 1000;
      btnEl.textContent = '⏸';
      runTimer(tid);
    } else {
      if (st.interval) clearInterval(st.interval);
      st.interval = null;
      st.remaining = remainingOf(st);
      st.paused = true;
      st.endAt = null;
      btnEl.textContent = '▶ 再開';
      dispEl.textContent = fmtCountdown(st.remaining);
    }
  } else {
    activeTimers[tid] = {
      endAt: Date.now() + totalSecs * 1000, remaining: totalSecs,
      paused: false, done: false, interval: null
    };
    dispEl.textContent = fmtCountdown(totalSecs);
    dispEl.className = 'timer-display';
    btnEl.textContent = '⏸';
    btnEl.style.display = '';
    runTimer(tid);
  }
  updateWakeLock();
}

function runTimer(tid) {
  var state = activeTimers[tid];
  if (!state) return;
  if (state.interval) clearInterval(state.interval);
  state.interval = setInterval(function () {
    var dispEl = el('disp-' + tid), btnEl = el('btn-' + tid);
    if (!dispEl) return;   // 再描画中。次のtickで復帰する
    var rest = remainingOf(state);
    if (rest <= 0) {
      clearInterval(state.interval);
      state.interval = null;
      state.done = true;
      state.remaining = 0;
      dispEl.textContent = '✅ 完了';
      dispEl.className = 'timer-display timer-done';
      if (btnEl) btnEl.style.display = 'none';
      beep();
      notifyDone();
      updateWakeLock();
    } else {
      state.remaining = rest;
      dispEl.textContent = fmtCountdown(rest);
    }
  }, 500);
}

function restoreTimers() {
  Object.keys(activeTimers).forEach(function (tid) {
    var st = activeTimers[tid];
    var dispEl = el('disp-' + tid), btnEl = el('btn-' + tid);
    if (!dispEl || !btnEl) {
      if (st && st.interval) clearInterval(st.interval);
      delete activeTimers[tid];
      return;
    }
    if (st.done) {
      dispEl.textContent = '✅ 完了';
      dispEl.className = 'timer-display timer-done';
      btnEl.style.display = 'none';
      return;
    }
    dispEl.textContent = fmtCountdown(remainingOf(st));
    btnEl.textContent = st.paused ? '▶ 再開' : '⏸';
    if (!st.paused && !st.interval) runTimer(tid);
  });
}

function beep() {
  try {
    var ctx = new (window.AudioContext || window.webkitAudioContext)();
    [0, 0.25, 0.5].forEach(function (t) {
      var osc = ctx.createOscillator(), gain = ctx.createGain();
      osc.connect(gain); gain.connect(ctx.destination);
      osc.frequency.value = 880; osc.type = 'sine';
      gain.gain.setValueAtTime(0.4, ctx.currentTime + t);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + t + 0.2);
      osc.start(ctx.currentTime + t); osc.stop(ctx.currentTime + t + 0.2);
    });
  } catch (e) {}
}

function notifyDone() {
  try {
    if (window.Notification && Notification.permission === 'granted') {
      new Notification('⏱ タイマー完了', { body: '調理タイマーが終了しました' });
    }
  } catch (e) {}
}

// 調理中に画面が消えないようにする
var wakeLock = null;
function updateWakeLock() {
  if (!navigator.wakeLock) return;
  var running = Object.keys(activeTimers).some(function (k) {
    return activeTimers[k] && !activeTimers[k].done && !activeTimers[k].paused;
  });
  if (running && !wakeLock) {
    navigator.wakeLock.request('screen').then(function (l) {
      wakeLock = l;
      l.addEventListener('release', function () { wakeLock = null; });
    }).catch(function () {});
  } else if (!running && wakeLock) {
    try { wakeLock.release(); } catch (e) {}
    wakeLock = null;
  }
}

// ---------------------------------------------------------------------------
// 代替食材 / 追加食材
// ---------------------------------------------------------------------------
var modalState = {};

function openSubModal(sc, i, ingKey) {
  var sub = (S[sc].subsMap[i] || {})[ingKey];
  if (!sub) return;
  modalState = { sc: sc, i: i, key: ingKey };
  el('modal-ing-name').textContent = ingKey;
  var current = (S[sc].subs[i] || {})[ingKey];

  var opts = '<button class="modal-opt' + (!current ? ' selected' : '') + '" onclick="selectSub(null)">'
    + '<div class="modal-opt-label">そのまま使う</div><div class="modal-opt-text">変更なし</div></button>';
  if (sub.alternative) {
    opts += '<button class="modal-opt' + (current && current !== '省略' ? ' selected' : '') + '" '
      + 'onclick="selectSub(\'' + jsq(sub.alternative) + '\')">'
      + '<div class="modal-opt-label">🔄 代替する</div>'
      + '<div class="modal-opt-text">' + esc(sub.alternative) + '</div></button>';
  }
  if (sub.can_omit) {
    opts += '<button class="modal-opt' + (current === '省略' ? ' selected' : '') + '" onclick="selectSub(\'省略\')">'
      + '<div class="modal-opt-label">❌ 省略する</div>'
      + '<div class="modal-opt-text">このレシピから除外</div>'
      + (sub.omit_note ? '<div class="modal-opt-note">💡 ' + esc(sub.omit_note) + '</div>' : '')
      + '</button>';
  }
  el('modal-opts').innerHTML = opts;
  openPanel('sub-modal');
}

function selectSub(val) {
  var sc = modalState.sc, i = modalState.i, key = modalState.key;
  var st = S[sc];
  if (!st.subs[i]) st.subs[i] = {};
  if (val === null) delete st.subs[i][key];
  else st.subs[i][key] = val;
  closeSubModal();
  rerender(sc);
}

function closeSubModal() { closePanel('sub-modal'); }
function closeModal(e) { if (e.target === el('sub-modal')) closeSubModal(); }

function toggleAddition(sc, i, name, btnEl) {
  var st = S[sc];
  if (!st.adds[i]) st.adds[i] = [];
  var pos = st.adds[i].indexOf(name);
  if (pos === -1) {
    st.adds[i].push(name);
    if (btnEl) btnEl.classList.add('chip-add--selected');
    showToast('✓ ' + name + ' を選択');
  } else {
    st.adds[i].splice(pos, 1);
    if (btnEl) btnEl.classList.remove('chip-add--selected');
    showToast('✕ ' + name + ' の選択を解除');
  }
  var bar = el(sc + '-rewrite-bar-' + i);
  if (bar) {
    var has = (st.subs[i] && Object.keys(st.subs[i]).length > 0) || st.adds[i].length > 0;
    bar.style.display = has ? 'block' : 'none';
  }
  if (sc === 'r') saveState();
}

// スコープに応じた再描画
function rerender(sc) {
  if (sc === 'k') renderKeptDetail();
  else renderResults({ recipes: S.r.list, generated_at: lastGenAt, candidate_count: lastCandCount, _fresh: false });
}

// ---------------------------------------------------------------------------
// レシピ更新
// ---------------------------------------------------------------------------
function applyUpdated(sc, i, u) {
  var r = S[sc].list[i];
  if (u.ingredients_ja && u.ingredients_ja.length) r.ingredients_ja = u.ingredients_ja;
  if (u.steps_ja && u.steps_ja.length)             r.steps_ja       = u.steps_ja;
  if (u.tips && u.tips.length)                     r.tips           = u.tips;
  if (sc === 'k') persistKeptDish();
}

function applyRewrite(sc, i) {
  var st = S[sc];
  var subs = st.subs[i] || {}, adds = st.adds[i] || [];
  if (!Object.keys(subs).length && !adds.length) return;

  var bar = el(sc + '-rewrite-bar-' + i);
  var btn = bar ? bar.querySelector('button') : null;
  if (btn) { btn.disabled = true; btn.textContent = '更新中…'; }

  postJSON('/api/rewrite', {
    recipe: st.list[i], selected_subs: subs, additions: adds,
    servings: lastServings, model: lastModel
  }).then(function (data) {
    if (data.error) { showToast('エラー: ' + data.error); return; }
    applyUpdated(sc, i, data.updated || {});
    st.subs[i] = {}; st.adds[i] = [];
    rerender(sc);
    showToast('✨ レシピを更新しました');
  }).catch(function (e) {
    showToast('通信エラー: ' + e.message);
  }).finally(function () {
    if (btn) { btn.disabled = false; btn.textContent = '✨ この変更でレシピを更新'; }
  });
}

function applyRewriteFromChat(sc, i) {
  var st = S[sc];
  var hist = st.chats[i] || [];
  var applied = st.applied[i] || 0;
  var pending = hist.slice(applied);
  if (!pending.length) { showToast('反映できる新しい会話がありません'); return; }

  var bar = el(sc + '-chat-rewrite-bar-' + i);
  var btn = bar ? bar.querySelector('button') : null;
  if (btn) { btn.disabled = true; btn.textContent = '更新中…'; }

  postJSON('/api/rewrite-from-chat', {
    recipe: st.list[i], chat_history: pending,
    servings: lastServings, model: lastModel
  }).then(function (data) {
    if (data.error) { showToast('エラー: ' + data.error); return; }
    applyUpdated(sc, i, data.updated || {});
    st.applied[i] = hist.length;
    if (sc === 'k') persistKeptDish();
    rerender(sc);
    showToast('✨ レシピを更新しました（このまま相談を続けられます）');
  }).catch(function (e) {
    showToast('通信エラー: ' + e.message);
  }).finally(function () {
    if (btn) { btn.disabled = false; btn.textContent = '💬 この会話を踏まえてレシピを更新'; }
  });
}

// ---------------------------------------------------------------------------
// チャット
// ---------------------------------------------------------------------------
function toggleChat(sc, i) {
  var body = el(sc + '-chat-body-' + i);
  body.classList.toggle('open');
  S[sc].open[i] = body.classList.contains('open');
}

function sendChat(sc, i) {
  var st = S[sc];
  var input = el(sc + '-chat-input-' + i);
  var msg = input.value.trim();
  if (!msg) return;
  input.value = '';
  st.drafts[i] = '';
  if (!st.chats[i]) st.chats[i] = [];

  appendMsg(sc, i, 'user', msg);
  var thinking = appendMsg(sc, i, 'thinking', '考え中…');
  var sendBtn = el(sc + '-chat-send-' + i);
  if (sendBtn) sendBtn.disabled = true;

  postJSON('/api/chat', {
    recipe: st.list[i], history: st.chats[i].slice(), message: msg, model: lastModel
  }).then(function (data) {
    if (thinking) thinking.remove();
    if (sendBtn) sendBtn.disabled = false;
    if (data.error) { appendMsg(sc, i, 'ai', '⚠ ' + data.error); return; }
    appendMsg(sc, i, 'ai', data.reply);
    st.chats[i].push({ role: 'user', content: msg });
    st.chats[i].push({ role: 'assistant', content: data.reply });
    st.open[i] = true;
    var bar = el(sc + '-chat-rewrite-bar-' + i);
    if (bar) bar.style.display = 'block';
    if (sc === 'k') persistKeptDish(); else saveState();
  }).catch(function (e) {
    if (thinking) thinking.remove();
    if (sendBtn) sendBtn.disabled = false;
    appendMsg(sc, i, 'ai', '⚠ 通信エラー: ' + e.message);
  });
}

// ---------------------------------------------------------------------------
// コピー
// ---------------------------------------------------------------------------
function copyText(text, onOk) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(onOk).catch(function () { fallbackCopy(text); onOk(); });
  } else {
    fallbackCopy(text); onOk();
  }
}
function fallbackCopy(text) {
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); } catch (e) {}
  document.body.removeChild(ta);
}
function copyRecipe(sc, i) {
  var r = S[sc].list[i];
  if (!r) return;
  copyText(recipeToText(r), function () {
    var btn = el(sc + '-copy-btn-' + i);
    if (!btn) return;
    var orig = btn.textContent;
    btn.textContent = 'コピーしました✓';
    btn.style.color = 'var(--green)';
    btn.style.borderColor = 'var(--green)';
    setTimeout(function () { btn.textContent = orig; btn.style.color = ''; btn.style.borderColor = ''; }, 1500);
  });
}

// ---------------------------------------------------------------------------
// 複数選択 → 買い物リスト / 並行調理
// ---------------------------------------------------------------------------
function toggleCardSelect(i, cb) {
  selectedIdx[i] = cb.checked;
  var card = el('r-recipe-card-' + i);
  if (card) card.classList.toggle('selected', cb.checked);
  updateMultiBar();
}
function clearSelection() {
  selectedIdx = {};
  document.querySelectorAll('.card-check').forEach(function (c) { c.checked = false; });
  document.querySelectorAll('#results .recipe-card').forEach(function (c) { c.classList.remove('selected'); });
  updateMultiBar();
}
function selectedRecipes() {
  return S.r.list.filter(function (_, i) { return selectedIdx[i]; });
}
function updateMultiBar() {
  var n = selectedRecipes().length;
  el('multi-bar').classList.toggle('show', n > 0);
  el('mb-count').textContent = n + '件選択中';
}
function targetRecipes() {
  var sel = selectedRecipes();
  return sel.length ? sel : S.r.list;
}

var lastShoppingList = null;

function openShoppingList() {
  var recipes = targetRecipes();
  if (!recipes.length) { showToast('レシピがありません'); return; }
  openShoppingListFor(recipes, lastServings, lastIngredients);
}

function openShoppingListFor(recipes, servings, have) {
  el('shopping-body').innerHTML = '<div class="panel-empty">🛒 買い物リストを作成しています…</div>';
  openPanel('shopping-panel');
  postJSON('/api/shopping-list', {
    recipes: recipes, servings: servings, have: have || [], model: lastModel
  }).then(function (data) {
    if (data.error) { el('shopping-body').innerHTML = '<div class="panel-empty">⚠ ' + esc(data.error) + '</div>'; return; }
    lastShoppingList = data.list;
    renderShoppingList(data.list);
  }).catch(function (e) {
    el('shopping-body').innerHTML = '<div class="panel-empty">⚠ 通信エラー: ' + esc(e.message) + '</div>';
  });
}

function renderShoppingList(list) {
  var html = (list.categories || []).map(function (cat, ci) {
    return '<div class="sl-cat"><h4>' + esc(cat.name) + '</h4>'
      + (cat.items || []).map(function (it) {
          return '<label class="sl-item">'
            + '<input type="checkbox" onchange="this.parentNode.classList.toggle(\'checked\',this.checked)">'
            + '<span class="nm">' + esc(it.name)
            + ((it.used_in && it.used_in.length) ? '<span class="sl-used">' + esc(it.used_in.join(' / ')) + '</span>' : '')
            + '</span>'
            + '<span class="amt">' + esc(it.amount || '') + '</span></label>';
        }).join('')
      + '</div>';
  }).join('');

  if (list.have_at_home && list.have_at_home.length) {
    html += '<div class="sl-cat sl-have"><h4>すでに手元にある</h4>'
      + list.have_at_home.map(function (it) {
          return '<div class="sl-item"><span class="nm">' + esc(it.name) + '</span>'
            + '<span class="amt">' + esc(it.amount || '') + '</span></div>';
        }).join('')
      + '</div>';
  }
  if (list.notes && list.notes.length) {
    html += '<div class="tl-notes">' + list.notes.map(function (n) { return '💡 ' + esc(n); }).join('<br>') + '</div>';
  }
  el('shopping-body').innerHTML = html || '<div class="panel-empty">材料がありません</div>';
}

function copyShoppingList() {
  if (!lastShoppingList) { showToast('リストがありません'); return; }
  var lines = ['🛒 買い物リスト', ''];
  (lastShoppingList.categories || []).forEach(function (c) {
    lines.push('■ ' + c.name);
    (c.items || []).forEach(function (it) { lines.push('□ ' + it.name + ' ' + (it.amount || '')); });
    lines.push('');
  });
  copyText(lines.join('\n'), function () { showToast('📋 コピーしました'); });
}

function openTimeline() {
  var recipes = targetRecipes();
  if (!recipes.length) { showToast('レシピがありません'); return; }
  openTimelineFor(recipes);
}

function openTimelineFor(recipes) {
  el('timeline-body').innerHTML = '<div class="panel-empty">⏱ 調理の段取りを組み立てています…</div>';
  openPanel('timeline-panel');
  postJSON('/api/timeline', { recipes: recipes, model: lastModel })
    .then(function (data) {
      if (data.error) { el('timeline-body').innerHTML = '<div class="panel-empty">⚠ ' + esc(data.error) + '</div>'; return; }
      renderTimeline(data.timeline);
    })
    .catch(function (e) {
      el('timeline-body').innerHTML = '<div class="panel-empty">⚠ 通信エラー: ' + esc(e.message) + '</div>';
    });
}

function renderTimeline(tl) {
  if (!tl || !tl.steps) {
    el('timeline-body').innerHTML = '<div class="panel-empty">タイムラインを作れませんでした</div>';
    return;
  }
  var html = '<div class="hint" style="margin-bottom:12px">合計 約' + esc(tl.total_min || '?') + '分</div>'
    + tl.steps.map(function (s) {
        return '<div class="tl-step' + (s.is_wait ? ' wait' : '') + '">'
          + '<div class="tl-time">' + esc(s.at_min) + '分</div>'
          + '<div class="tl-body">'
          + '<div class="tl-recipe">' + esc(s.recipe || '') + (s.duration_min ? ' · ' + esc(s.duration_min) + '分' : '') + '</div>'
          + '<div class="tl-text">' + esc(s.text) + '</div>'
          + '</div></div>';
      }).join('');
  if (tl.notes && tl.notes.length) {
    html += '<div class="tl-notes">' + tl.notes.map(function (n) { return '💡 ' + esc(n); }).join('<br>') + '</div>';
  }
  el('timeline-body').innerHTML = html;
}

// ---------------------------------------------------------------------------
// サマリーと詳細カードの連動
// ---------------------------------------------------------------------------
function scrollToRecipe(i) {
  var target = el('r-recipe-card-' + i);
  if (!target) return;
  target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  highlightSummary(i);
}
function highlightSummary(i) {
  document.querySelectorAll('.sum-card').forEach(function (e) { e.classList.remove('current'); });
  document.querySelectorAll('#results .recipe-card').forEach(function (e) { e.classList.remove('current'); });
  var s = el('sum-card-' + i), rc = el('r-recipe-card-' + i);
  if (s) s.classList.add('current');
  if (rc) rc.classList.add('current');
}
function setupCardObserver() {
  if (cardObserver) { cardObserver.disconnect(); cardObserver = null; }
  var cards = document.querySelectorAll('#results .recipe-card');
  if (!cards.length || !('IntersectionObserver' in window)) return;
  cardObserver = new IntersectionObserver(function (entries) {
    var best = null;
    entries.forEach(function (e) {
      if (e.isIntersecting && (!best || e.intersectionRatio > best.intersectionRatio)) best = e;
    });
    if (best) highlightSummary(parseInt(best.target.getAttribute('data-idx'), 10));
  }, { threshold: [0, 0.25, 0.5, 0.75, 1] });
  cards.forEach(function (c) { cardObserver.observe(c); });
  highlightSummary(0);
}

// ---------------------------------------------------------------------------
// 状態の保存 / 復元
// ---------------------------------------------------------------------------
function softReset() {
  shownTitles = [];
  S.r = newScope();
  selectedIdx = {};
  accState = {};
  Object.keys(activeTimers).forEach(function (id) {
    if (activeTimers[id] && activeTimers[id].interval) clearInterval(activeTimers[id].interval);
  });
  activeTimers = {};
  updateWakeLock();
  if (cardObserver) { cardObserver.disconnect(); cardObserver = null; }
  el('results').innerHTML = '';
  updateMultiBar();
  hideError();
  saveState();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function resetAll() {
  tags = []; lastIngredients = []; lastMood = ''; lastServings = 2;
  lastMaxTime = ''; lastDrink = '';
  el('mood').value = '';
  el('servings').value = '2';
  el('max-time').value = '';
  el('drink').value = '';
  el('ingredient-input').value = '';
  renderTags();
  try { localStorage.removeItem(LS_STATE); } catch (e) {}
  softReset();
}

function saveState() {
  try {
    localStorage.setItem(LS_STATE, JSON.stringify({
      recipes: S.r.list, generated_at: lastGenAt, candidate_count: lastCandCount,
      tags: tags, mood: lastMood, servings: lastServings, max_time: lastMaxTime,
      drink: lastDrink, model: lastModel, shownTitles: shownTitles,
      chats: S.r.chats, applied: S.r.applied, subs: S.r.subs, adds: S.r.adds
    }));
  } catch (e) {}
}

function restoreState() {
  try {
    var raw = localStorage.getItem(LS_STATE);
    if (!raw) return;
    var st = JSON.parse(raw);

    // 入力条件は提案結果の有無に関わらず復元する
    lastIngredients = st.tags || [];
    tags = lastIngredients.slice();
    lastMood = st.mood || '';
    lastServings = st.servings || 2;
    lastMaxTime = st.max_time || '';
    lastDrink = st.drink || '';
    lastModel = st.model || '';
    shownTitles = st.shownTitles || [];

    el('mood').value = lastMood;
    el('servings').value = lastServings;
    el('max-time').value = lastMaxTime;
    el('drink').value = lastDrink;
    renderTags();

    if (st.recipes && st.recipes.length) {
      S.r = newScope();
      S.r.list    = st.recipes;
      S.r.chats   = st.chats || {};
      S.r.applied = st.applied || {};
      S.r.subs    = st.subs || {};
      S.r.adds    = st.adds || {};
      lastGenAt = st.generated_at || '';
      lastCandCount = st.candidate_count || st.recipes.length;
      renderResults({
        recipes: st.recipes, generated_at: lastGenAt,
        candidate_count: lastCandCount, _fresh: false
      });
    }
  } catch (e) {
    console.error('[RecipeApp] restore failed:', e);
  }
}

// ---------------------------------------------------------------------------
// 起動
// ---------------------------------------------------------------------------
function loadVersion() {
  return fetch('/api/version').then(function (r) { return r.json(); }).then(function (d) {
    photosEnabled = d.photos_enabled !== false;
    el('deploy-info').textContent = 'deployed: ' + d.deployed_at
      + (d.theory_loaded === false ? '  ⚠ 料理セオリー未読込' : '');
    var sel = el('model');
    if (d.models && d.models.length) {
      sel.innerHTML = d.models.map(function (m) {
        return '<option value="' + esc(m.id) + '">' + esc(m.label) + '</option>';
      }).join('');
      if (lastModel) sel.value = lastModel;
      if (!sel.value) sel.selectedIndex = 0;
      lastModel = sel.value;
    }
  }).catch(function () {});
}

function registerSW() {
  // 非セキュアコンテキストやプライベートモードでは navigator.serviceWorker が
  // 存在しても中身が無いことがあるため、実体を確認してから登録する
  try {
    if (!navigator.serviceWorker || !navigator.serviceWorker.register) return;
    navigator.serviceWorker.register('/sw.js').catch(function () {});
  } catch (e) {}
}

function init() {
  setupTagInput();
  updateHistCount();
  restoreState();
  loadVersion();
  registerSW();
  el('model').addEventListener('change', function () { lastModel = this.value; saveState(); });
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) restoreTimers();
  });
  // タイマーを初めて押したときに通知許可を求める（起動直後には出さない）
  if (window.Notification && Notification.permission === 'default') {
    document.addEventListener('click', function once(e) {
      if (e.target && e.target.classList && e.target.classList.contains('timer-btn')) {
        try { Notification.requestPermission(); } catch (err) {}
        document.removeEventListener('click', once);
      }
    });
  }
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
