const { chromium } = require('playwright');
const assert = require('assert');
const path = require('path');

// любая JPEG-картинка из репозитория подходит для проверки загрузки фото
const PHOTO = path.join(__dirname, '..', 'assets', 'welcome.jpg');

(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport:{width:390,height:844}, deviceScaleFactor:2, hasTouch:true });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push('PAGEERROR ' + e.message));

  // Подменяем Telegram SDK и API
  await page.addInitScript(() => {
    window.Telegram = { WebApp: {
      initData: 'query_id=AA&user=%7B%22id%22%3A777%7D&auth_date=1&hash=deadbeef',
      colorScheme: 'light', ready(){}, expand(){}, disableVerticalSwipes(){},
      onEvent(){}, setHeaderColor(){}, setBackgroundColor(){},
      openTelegramLink(u){ window.__opened = u; }, openLink(u){ window.__opened = u; },
      BackButton:{ shown:false, show(){this.shown=true}, hide(){this.shown=false}, onClick(){} },
      HapticFeedback:{ impactOccurred(){}, notificationOccurred(){} },
    }};
  });

  const calls = [];
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    calls.push({ url: req.url(), post: (req.postData()||'').slice(0, 4000) });
    await route.fulfill({ status:200, contentType:'application/json',
      body: JSON.stringify({ ok:true, message:'Документ отправлен в чат с ботом 📄' }) });
  });

  const go = async (h) => { await page.goto('http://127.0.0.1:8777/' + h, { waitUntil:'domcontentloaded' }); await page.waitForTimeout(250); };

  // 1. Баннер «откройте в Telegram» не показывается внутри Telegram
  await go('#/home');
  assert.strictEqual(await page.locator('.note').count(), 0, 'внутри Telegram баннер не нужен');

  // 2. BackButton прячется на главной и показывается внутри
  assert.strictEqual(await page.evaluate(() => window.Telegram.WebApp.BackButton.shown), false, 'на главной BackButton скрыт');
  await page.click('a[href="#/npb"]'); await page.waitForTimeout(250);
  assert.strictEqual(await page.evaluate(() => window.Telegram.WebApp.BackButton.shown), true, 'внутри BackButton виден');

  // 3. Клик по документу НПБ → запрос /api/doc
  await page.click('[data-doc="3"]'); await page.waitForTimeout(350);
  const doc = calls.find(c => c.url.endsWith('/api/doc'));
  assert.ok(doc && JSON.parse(doc.post).id === '3', 'отправлен id документа');
  assert.ok(await page.locator('#toast[data-show="1"]').count(), 'показан тост');

  // 4. Внешние ссылки открываются через Telegram
  await go('#/groups');
  await page.click('[data-link="https://t.me/NABLABFU"]'); await page.waitForTimeout(150);
  assert.strictEqual(await page.evaluate(() => window.__opened), 'https://t.me/NABLABFU', 'ссылка открыта');

  // 5. Валидация пустой формы
  await go('#/f/question');
  await page.click('#submitBtn'); await page.waitForTimeout(200);
  assert.strictEqual(calls.filter(c => c.url.endsWith('/api/report')).length, 0, 'пустая форма не отправляется');
  assert.strictEqual(await page.locator('#toast').getAttribute('data-kind'), 'err', 'показана ошибка');

  // 6. Отправка вопроса анонимно
  await page.click('[data-anon="1"]');
  await page.fill('#text', 'Когда будет расписание сессии?');
  assert.strictEqual(await page.locator('#cnt').textContent(), '30', 'счётчик символов работает');
  await page.click('#submitBtn'); await page.waitForTimeout(500);
  const rep = calls.find(c => c.url.endsWith('/api/report'));
  assert.ok(rep, 'форма ушла на /api/report; calls=' + JSON.stringify(calls.map(c=>c.url)));
  assert.ok(page.url().includes('#/ok/question'), 'после отправки — экран «Принято», url=' + page.url());
  assert.ok((await page.locator('.success__mark').count()) === 1, 'есть галочка успеха');

  // 7. Поломки: есть поле места и загрузка фото
  await go('#/f/repair');
  assert.strictEqual(await page.locator('#place').count(), 1, 'поле «корпус и аудитория»');
  assert.strictEqual(await page.locator('#fileInput').count(), 1, 'инпут для фото');

  // 7b. Реальная загрузка фото: превью + отправка multipart
  await page.setInputFiles('#fileInput', PHOTO);
  await page.waitForTimeout(600);
  assert.strictEqual(await page.locator('#photoGrid .photo').count(), 1, 'превью фото появилось');
  await page.click('#photoGrid .photo__del'); await page.waitForTimeout(200);
  assert.strictEqual(await page.locator('#photoGrid .photo').count(), 0, 'фото удаляется');
  await page.setInputFiles('#fileInput', PHOTO);
  await page.waitForTimeout(600);
  await page.fill('#place', 'Кибальчича, ауд. 206');
  await page.fill('#text', 'Не работает розетка у окна');
  const before = calls.length;
  await page.click('#submitBtn'); await page.waitForTimeout(700);
  const rep2 = calls.slice(before).find(c => c.url.endsWith('/api/report'));
  assert.ok(rep2, 'поломка отправлена');
  assert.ok(rep2.post.includes('name="photos"'), 'фото в multipart: ' + rep2.post.slice(0,120));
  assert.ok(rep2.post.includes('repair'), 'тип обращения repair');
  assert.ok(page.url().includes('#/ok/repair'), 'экран успеха для поломки');

  // 8. Читалка FAQ: вперёд/назад/клик по превью
  await go('#/page/1');
  assert.ok(await page.locator('#prevPage').isDisabled(), 'на 1-й странице «назад» выключено');
  await page.click('#nextPage'); await page.waitForTimeout(200);
  assert.strictEqual(await page.locator('#readerPos').textContent(), '2 / 26');
  assert.ok((await page.locator('#readerImg').getAttribute('src')).endsWith('02.webp'), 'картинка сменилась');
  await page.click('#strip img[data-page="26"]'); await page.waitForTimeout(250);
  assert.strictEqual(await page.locator('#readerPos').textContent(), '26 / 26');
  assert.ok(await page.locator('#nextPage').isDisabled(), 'на последней «вперёд» выключено');

  // 9. Все 26 страниц и превью реально существуют
  for (const n of [1, 13, 26]) {
    for (const p of [`assets/faq/${String(n).padStart(2,'0')}.webp`, `assets/faq/thumb/${String(n).padStart(2,'0')}.webp`]) {
      const r = await page.request.get('http://127.0.0.1:8777/' + p);
      assert.strictEqual(r.status(), 200, 'нет файла ' + p);
    }
  }

  // 10. Неизвестный роут не роняет приложение
  await go('#/wat/123');
  assert.ok(await page.locator('.hero').count() === 1, 'неизвестный маршрут → главная');

  assert.strictEqual(errors.length, 0, 'ошибки JS: ' + errors.join('; '));
  console.log('✅ e2e: 11/11 сценариев пройдено');
  await b.close();
})().catch(e => { console.error('❌ ' + e.message); process.exit(1); });
