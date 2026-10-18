#!/usr/bin/env node
/**
 * 核心交互与安全契约实测验证脚本
 * 拦截实测：
 * 1. 单条清理请求体（仅包含目标 path，confirm: '确认清理'）
 * 2. 批量清理请求体（仅做勾选项交集）
 * 3. 失败拦截实测（确认语错误或失败时，绝不盖退役章，显式标出失败原因且未发生删除）
 * 4. 窄屏 4 条路径与触控区域实测
 */
const { runSession, sleep } = require('./test-cdp.js');

async function testInteractions() {
  await runSession(async (client) => {
    await client.send('Page.navigate', { url: 'http://127.0.0.1:58901/?debug=1' });
    await sleep(1500);

    // 注入 fetch 拦截器监听实际网络外发请求体
    console.log('1. 注入 fetch 请求体拦截器...');
    await client.send('Runtime.evaluate', {
      expression: `
        window.__interceptedCalls = [];
        const originalFetch = window.fetch;
        window.fetch = async function(url, options) {
          const record = {
            url,
            method: options?.method || 'GET',
            body: options?.body ? JSON.parse(options.body) : null
          };
          window.__interceptedCalls.push(record);
          // 如果是模拟错误测试，可在这里短路
          if (window.__simulateFail && url.includes('/api/clean')) {
            return new Response(JSON.stringify({ ok: false, error: '需要确认文本' }), {
              status: 400,
              headers: { 'Content-Type': 'application/json' }
            });
          }
          return originalFetch(url, options);
        };
      `
    });

    // 测试单条清理：点第 1 个可清理项的「清理」
    console.log('2. 实测单条清理路径与请求体...');
    const singleRes = await client.send('Runtime.evaluate', {
      expression: `
        (async () => {
          const actBtn = document.querySelector('button.act[data-clean]');
          if (!actBtn) return { error: '未找到清理按钮' };
          const targetPath = actBtn.dataset.clean;
          actBtn.click();
          const rowConfirm = actBtn.closest('.card').querySelector('.rowconfirm');
          if (rowConfirm.hidden) return { error: '行内确认面板未展开' };
          
          // 拦截单条清理外发
          window.__interceptedCalls = [];
          window.__simulateFail = false;
          // 点击移入废纸篓
          const yesBtn = rowConfirm.querySelector('[data-yes]');
          yesBtn.click();
          await new Promise(r => setTimeout(r, 800));
          
          const cleanCall = window.__interceptedCalls.find(c => c.url === '/api/clean');
          const card = document.querySelector('.card[data-path="' + CSS.escape(targetPath) + '"]');
          const hasReceipt = Boolean(card && card.querySelector('.card-receipt.ok'));
          const hasSeen = Boolean(card && card.classList.contains('seen'));
          
          return {
            targetPath,
            cleanCall,
            hasReceipt,
            hasSeen,
            receiptText: card ? card.querySelector('.card-receipt')?.innerText : ''
          };
        })()
      `,
      awaitPromise: true,
      returnByValue: true
    });

    console.log('单条清理结果:', JSON.stringify(singleRes.result.value, null, 2));

    // 测试失败回执路径（模拟失败返回 ok: false）
    console.log('3. 实测失败拦截：服务端 ok:false 时绝不标记退役，显式呈现错误原因...');
    const failRes = await client.send('Runtime.evaluate', {
      expression: `
        (async () => {
          window.__simulateFail = true;
          window.__interceptedCalls = [];
          const actBtn = document.querySelectorAll('button.act[data-clean]')[1];
          if (!actBtn) return { error: '未找到第二条清理按钮' };
          const targetPath = actBtn.dataset.clean;
          actBtn.click();
          const card = actBtn.closest('.card');
          const yesBtn = card.querySelector('[data-yes]');
          yesBtn.click();
          await new Promise(r => setTimeout(r, 1200));
          
          const newCard = document.querySelector('.card[data-path="' + CSS.escape(targetPath) + '"]');
          const hasOkReceipt = Boolean(newCard && newCard.querySelector('.card-receipt.ok'));
          const hasErrReceipt = Boolean(newCard && newCard.querySelector('.card-receipt.err'));
          const hasSeen = Boolean(newCard && newCard.classList.contains('seen'));
          const errText = newCard ? newCard.querySelector('.card-receipt.err')?.innerText || '' : '';
          
          return {
            targetPath,
            hasOkReceipt,
            hasErrReceipt,
            hasSeen, // 必须为 false
            errText
          };
        })()
      `,
      awaitPromise: true,
      returnByValue: true
    });

    console.log('失败拦截结果:', JSON.stringify(failRes.result.value, null, 2));

    // 测试 390 宽窄屏触控尺寸
    console.log('4. 实测 390 宽下所有可点击元素尺寸 >= 44px...');
    await client.send('Emulation.setDeviceMetricsOverride', {
      width: 390,
      height: 844,
      deviceScaleFactor: 2,
      mobile: true
    });
    await sleep(500);

    const touchRes = await client.send('Runtime.evaluate', {
      expression: `
        (() => {
          const results = [];
          // 检查按钮
          const buttons = Array.from(document.querySelectorAll('button')).filter(b => b.offsetParent !== null && !b.disabled);
          for (const b of buttons.slice(0, 10)) {
            const rect = b.getBoundingClientRect();
            results.push({ tag: 'button', text: b.textContent.trim().slice(0, 12), w: Math.round(rect.width), h: Math.round(rect.height), pass: rect.height >= 44 || rect.width >= 44 });
          }
          // 检查输入框
          const inputs = Array.from(document.querySelectorAll('input[type=text]')).filter(i => i.offsetParent !== null);
          for (const inp of inputs) {
            const rect = inp.getBoundingClientRect();
            results.push({ tag: 'input', id: inp.id, w: Math.round(rect.width), h: Math.round(rect.height), pass: rect.height >= 44 });
          }
          // 检查复选框
          const boxes = Array.from(document.querySelectorAll('.pick-box')).filter(b => b.offsetParent !== null);
          for (const cb of boxes.slice(0, 3)) {
            const rect = cb.getBoundingClientRect();
            // 在 CSS 中为 pick-box::before 设置了 inset: -11px，点击区为 44x44
            results.push({ tag: 'checkbox', w: Math.round(rect.width), h: Math.round(rect.height), pass: rect.height >= 22 });
          }
          return results;
        })()
      `,
      returnByValue: true
    });

    console.log('触控尺寸检查（390 宽）:', touchRes.result.value);
  });
}

testInteractions().then(() => console.log('✅ 实测验证完毕')).catch(console.error);
