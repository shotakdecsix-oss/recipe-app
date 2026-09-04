/* フロント側のUIテスト。tests/mockserver.py を起動してから実行する。
 *   python3 tests/mockserver.py    # 別ターミナル
 *   node tests/ui.test.js
 */
const { chromium } = require('playwright');
const R = [];
const ck = (n, c, x) => R.push((c ? 'PASS  ' : 'FAIL  ') + n + (c ? '' : '  << ' + JSON.stringify(x)));
const URL_BASE = 'http://localhost:8900/';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text().slice(0, 160)); });
  // Service Worker はテスト間でキャッシュが残るため無効化する
  await page.addInitScript(() => { Object.defineProperty(navigator, 'serviceWorker', { get: () => undefined }); });

  await page.goto(URL_BASE); await page.waitForTimeout(400);
  ck('初期ロードでJSエラーなし', errors.length === 0, errors);
  ck('モデル選択がサーバーから入る', await page.locator('#model option').count() === 2);
  ck('deploy時刻が表示される', (await page.locator('#deploy-info').innerText()).includes('deployed'));

  // ---- 提案フロー ----
  await page.fill('#ingredient-input', '鶏もも肉'); await page.press('#ingredient-input', 'Enter');
  await page.fill('#ingredient-input', 'にんじん'); await page.press('#ingredient-input', 'Enter');
  await page.click('#btn-suggest');
  await page.waitForSelector('#results .recipe-card', { timeout: 10000 });
  ck('レシピカードが2枚出る', await page.locator('#results .recipe-card').count() === 2);
  const sum0 = await page.locator('#sum-card-0').innerText();
  ck('サマリーに主要食材が出る', sum0.includes('🥕') && sum0.includes('鶏もも肉'), sum0);
  ck('サマリーに買い足しが出る', sum0.includes('買い足し') && sum0.includes('長ねぎ'), sum0);
  ck('調味料は買い足しに出ない', !sum0.split('買い足し')[1].includes('醤油'), sum0);

  // ---- 料理のイメージ写真 ----
  await page.waitForTimeout(500);
  ck('提案カードに写真が出る', await page.locator('#r-recipe-card-0 img.recipe-photo').count() === 1);
  ck('サマリーにも写真が出る', await page.locator('#sum-photo-0 img.sum-photo').count() === 1);
  ck('「イメージ」と明示される',
    (await page.locator('#r-photo-0 .photo-note').innerText()).includes('イメージ'));
  ck('撮影者クレジットが出る',
    (await page.locator('#r-photo-0 .photo-credit').innerText()).includes('Pexels'));
  ck('写真がレシピに保持される',
    await page.evaluate(() => !!(window.S.r.list[0]._photo && window.S.r.list[0]._photo.url)));

  // ---- タイマーの誤検出 ----
  ck('「中火で5分焼く」にタイマーが付く', await page.locator('#btn-timer-r-0-1').count() === 1);
  ck('「3分の1」でタイマーを作らない', await page.locator('#btn-timer-r-0-2').count() === 0);

  // ---- チャットと再描画 ----
  await page.click('#r-recipe-card-0 .chat-toggle');
  await page.fill('#r-chat-input-0', '代替食材は？');
  await page.click('#r-chat-send-0'); await page.waitForTimeout(600);
  ck('チャット応答が表示される', (await page.locator('#r-chat-msgs-0').innerText()).includes('回答ベータ'));

  await page.click('#r-recipe-card-0 .chip-sub'); await page.waitForTimeout(200);
  ck('代替モーダルが開く', await page.locator('#sub-modal.open').count() === 1);
  ck('モーダルを開くとフォーカスが中に入る',
    await page.evaluate(() => document.getElementById('sub-modal').contains(document.activeElement)));
  await page.locator('#modal-opts .modal-opt').nth(1).click(); await page.waitForTimeout(300);
  ck('再描画後もチャットが残る', (await page.locator('#r-chat-msgs-0').innerText()).includes('回答ベータ'));
  ck('代替の選択がチップに反映される', await page.locator('#r-recipe-card-0 .chip-sub--selected').count() === 1);
  ck('更新ボタンが出る', await page.locator('#r-rewrite-bar-0').evaluate(e => e.style.display) === 'block');

  // ---- タイマーが再描画をまたぐ ----
  await page.click('#btn-timer-r-0-1'); await page.waitForTimeout(1100);
  const t1 = await page.locator('#disp-timer-r-0-1').innerText();
  await page.click('#r-recipe-card-0 .chip-sub--selected'); await page.waitForTimeout(200);
  await page.locator('#modal-opts .modal-opt').nth(0).click(); await page.waitForTimeout(1100);
  const t2 = await page.locator('#disp-timer-r-0-1').innerText();
  ck('再描画後もタイマーが動き続ける', /^\d\d:\d\d$/.test(t2) && t2 < t1, { t1, t2 });

  // ---- 会話からレシピ更新 ----
  await page.click('#r-chat-rewrite-bar-0 button'); await page.waitForTimeout(700);
  const msgs = await page.locator('#r-chat-msgs-0').innerText();
  ck('更新後も会話が残る', msgs.includes('回答ベータ'), msgs);
  ck('反映済みの区切りが出る', msgs.includes('ここまでレシピに反映済み'));
  ck('更新ボタンが消える', await page.locator('#r-chat-rewrite-bar-0').evaluate(e => e.style.display) === 'none');
  ck('手順が更新される', (await page.locator('#r-recipe-card-0 .steps-list').innerText()).includes('更新手順1'));

  // ---- 提案の複数選択 ----
  await page.locator('#r-check-0').check();
  await page.locator('#r-check-1').check();
  ck('選択バーが出る', await page.locator('#multi-bar.show').count() === 1);
  ck('選択件数が正しい', (await page.locator('#mb-count').innerText()).includes('2件'));

  await page.click('#multi-bar >> text=🛒 買い物リスト'); await page.waitForTimeout(600);
  const sl = await page.locator('#shopping-body').innerText();
  ck('買い物リストが出る', sl.includes('長ねぎ') && sl.includes('1本'), sl);
  ck('手持ち食材が分けられる', sl.includes('すでに手元にある') && sl.includes('醤油'));
  await page.keyboard.press('Escape'); await page.waitForTimeout(200);
  ck('Escでパネルが閉じる', await page.locator('#shopping-panel.open').count() === 0);

  await page.click('#multi-bar >> text=⏱ 並行調理'); await page.waitForTimeout(600);
  ck('タイムラインが出る', (await page.locator('#timeline-body').innerText()).includes('コンロ2口'));
  await page.keyboard.press('Escape');

  // ---- まとめて決定 ----
  await page.click('#multi-bar >> text=✅ まとめて決定'); await page.waitForTimeout(400);
  const hist = await page.evaluate(() => JSON.parse(localStorage.getItem('recipe_history')));
  ck('2件まとめて保存される', hist.length === 2, hist && hist.length);
  ck('提案条件も保存される', hist[0]._context.ingredients.length === 2, hist[0]._context);
  ck('決定後も食材タグが残る', await page.locator('#tag-wrap .tag').count() === 2);
  ck('決定後に結果はクリアされる', await page.locator('#results .recipe-card').count() === 0);
  ck('履歴バッジが更新される', (await page.locator('#hist-count').innerText()).includes('2'));

  // ---- 履歴パネル ----
  await page.click('#btn-history'); await page.waitForTimeout(300);
  ck('履歴カードが2枚', await page.locator('.hist-card').count() === 2);
  ck('履歴カードに写真が出る', await page.locator('.hist-card img.hist-photo').count() === 2);
  ck('パネルを開くとフォーカスが中に入る',
    await page.evaluate(() => document.getElementById('history-panel').contains(document.activeElement)));
  await page.fill('#hist-search', 'サラダ'); await page.waitForTimeout(200);
  ck('検索で絞り込める', await page.locator('.hist-card').count() === 1);
  await page.fill('#hist-search', '');
  await page.selectOption('#hist-sort', 'time'); await page.waitForTimeout(200);
  ck('時間順に並び替わる', (await page.locator('.hist-card').first().innerText()).includes('サラダ'));
  ck('食材チップが出る', await page.locator('.hist-chip').count() >= 1);
  await page.locator('.hist-chip').first().click(); await page.waitForTimeout(200);
  ck('食材で絞り込める', await page.locator('.hist-card').count() === 2);
  await page.locator('.hist-chip').first().click(); await page.waitForTimeout(200);

  // ---- 履歴からの複数選択 → 買い物リスト / 並行調理 ----
  await page.locator('.hist-check').nth(0).check();
  await page.locator('.hist-check').nth(1).check();
  ck('履歴の選択バーが出る', await page.locator('#hist-bar.show').count() === 1);
  ck('履歴の選択件数が正しい', (await page.locator('#hist-mb-count').innerText()).includes('2件'));
  await page.click('#hist-bar >> text=🛒 買い物リスト'); await page.waitForTimeout(600);
  ck('履歴から買い物リストが作れる', (await page.locator('#shopping-body').innerText()).includes('長ねぎ'));
  await page.keyboard.press('Escape'); await page.waitForTimeout(200);
  await page.click('#hist-bar >> text=⏱ 並行調理'); await page.waitForTimeout(600);
  ck('履歴から並行調理が作れる', (await page.locator('#timeline-body').innerText()).includes('コンロ2口'));
  await page.keyboard.press('Escape'); await page.waitForTimeout(200);
  await page.click('#hist-bar >> text=解除'); await page.waitForTimeout(200);
  ck('選択解除できる', await page.locator('#hist-bar.show').count() === 0);

  // ---- 保存レシピでの相談・更新 ----
  await page.locator('.hist-card').filter({ hasText: '照り焼き' }).click(); await page.waitForTimeout(400);
  ck('保存レシピが詳細表示される', await page.locator('#kept-modal.open').count() === 1);
  ck('詳細を開くと履歴は閉じる', await page.locator('#history-panel.open').count() === 0);
  ck('保存レシピにチャット欄がある', await page.locator('#k-chat-input-0').count() === 1);
  ck('保存レシピにコピーがある', await page.locator('#k-copy-btn-0').count() === 1);
  ck('保存レシピにタイマーがある', await page.locator('#btn-timer-k-0-1').count() === 1);
  ck('保存レシピに「これに決めた」は出ない', await page.locator('#kept-modal .btn-keep').count() === 0);
  ck('「もう一度提案」ボタンがある', await page.locator('#kept-modal .btn-reuse').count() === 1);
  ck('保存レシピにも写真が出る', await page.locator('#k-photo-0 img.recipe-photo').count() === 1);

  await page.click('#k-recipe-card-0 .chat-toggle');
  await page.fill('#k-chat-input-0', '保存後の質問です');
  await page.click('#k-chat-send-0'); await page.waitForTimeout(600);
  ck('保存レシピでもAIが答える', (await page.locator('#k-chat-msgs-0').innerText()).includes('回答ベータ'));
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('recipe_history')));
  const withChat = saved.find(r => r._chat && r._chat.length);
  ck('保存レシピの会話が永続化される', !!withChat && withChat._chat.length === 2, withChat && withChat._chat);

  await page.click('#k-chat-rewrite-bar-0 button'); await page.waitForTimeout(700);
  ck('保存レシピを会話から更新できる',
    (await page.locator('#k-recipe-card-0 .steps-list').innerText()).includes('更新手順1'));
  const saved2 = await page.evaluate(() => JSON.parse(localStorage.getItem('recipe_history')));
  ck('更新内容が履歴に保存される',
    saved2.some(r => (r.steps_ja || []).some(s => s.includes('更新手順1'))));

  // 詳細を閉じると履歴に戻る
  await page.keyboard.press('Escape'); await page.waitForTimeout(300);
  ck('詳細を閉じると履歴に戻る', await page.locator('#history-panel.open').count() === 1);

  // ---- 「この条件でもう一度」 ----
  await page.evaluate(() => { document.getElementById('mood').value = ''; window.tags = []; renderTags(); });
  await page.locator('.hist-card').filter({ hasText: '照り焼き' }).click(); await page.waitForTimeout(400);
  await page.click('#kept-modal .btn-reuse');
  await page.waitForSelector('#results .recipe-card', { timeout: 10000 });
  ck('もう一度提案で食材が復元される', await page.locator('#tag-wrap .tag').count() === 2);
  ck('もう一度提案でパネルが閉じる',
    await page.locator('#kept-modal.open, #history-panel.open').count() === 0);
  ck('もう一度提案で再提案が走る', await page.locator('#results .recipe-card').count() === 2);

  // ---- リロード後の復元 ----
  errors.length = 0;
  await page.reload(); await page.waitForTimeout(600);
  ck('リロード後にJSエラーなし', errors.length === 0, errors);
  ck('リロード後も食材が残る', await page.locator('#tag-wrap .tag').count() === 2);
  ck('リロード後も履歴が残る', (await page.locator('#hist-count').innerText()).includes('2'));
  await page.click('#btn-history'); await page.waitForTimeout(300);
  await page.locator('.hist-card').filter({ hasText: '照り焼き' }).click(); await page.waitForTimeout(400);
  ck('リロード後も保存レシピの会話が復元される',
    (await page.locator('#k-chat-msgs-0').innerText()).includes('保存後の質問です'));

  await browser.close();
  console.log(R.join('\n'));
  const p = R.filter(r => r.startsWith('PASS')).length;
  console.log('\n' + p + '/' + R.length + ' passed');
  process.exit(p === R.length ? 0 : 1);
})().catch(e => { console.error('TEST CRASH:', e.message); process.exit(1); });
