#!/usr/bin/env node
/**
 * 视口整页与特写截图工具（1440 宽 / 390 宽 / 1x 与 2x 特写），支持 DPR 与 ?debug=1 状态钉死
 * 用法：node tools/snap.js [desktop|mobile|drum|all]
 */
const { runSession, sleep } = require('./test-cdp.js');
const fs = require('fs');
const path = require('path');

const REVIEW_DIR = path.join(__dirname, '..', '.impeccable', 'review');

async function captureViewport(client, { width, height, dpr, mobile, outName, forceOcc = 0.661, onReady }) {
  console.log(`📸 正在截取: ${outName} (${width}x${height}, DPR=${dpr})...`);
  
  await client.send('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor: dpr,
    mobile: Boolean(mobile)
  });

  await client.send('Page.navigate', { url: 'http://127.0.0.1:58901/?debug=1' });
  await sleep(1500);

  // 钉死动画状态（避免在无头浏览器中低帧率导致截到半中间状态）
  if (forceOcc != null) {
    await client.send('Runtime.evaluate', {
      expression: `if (window.__drum && window.__drum.force) { window.__drum.force(${forceOcc}); }`
    });
    await sleep(400);
  }

  if (onReady) {
    await onReady(client);
    await sleep(400);
  }

  // 获取完整页面高度
  const metrics = await client.send('Page.getLayoutMetrics');
  const contentHeight = Math.ceil(metrics.contentSize.height);

  // 设置视口为整页高度以拍摄整页
  await client.send('Emulation.setDeviceMetricsOverride', {
    width,
    height: contentHeight,
    deviceScaleFactor: dpr,
    mobile: Boolean(mobile)
  });
  await sleep(500);

  const shot = await client.send('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: true
  });

  fs.mkdirSync(REVIEW_DIR, { recursive: true });
  const outPath = path.join(REVIEW_DIR, outName);
  fs.writeFileSync(outPath, Buffer.from(shot.data, 'base64'));
  const size = fs.statSync(outPath).size;
  console.log(`✅ 已保存: ${outPath} (${(size / 1024).toFixed(1)} KB)`);
}

async function captureElement(client, { width, height, dpr, selector, outName, forceOcc = 0.661 }) {
  console.log(`📸 正在截取元素特写: ${outName} (DPR=${dpr})...`);
  
  await client.send('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor: dpr,
    mobile: false
  });

  await client.send('Page.navigate', { url: 'http://127.0.0.1:58901/?debug=1' });
  await sleep(1500);

  if (forceOcc != null) {
    await client.send('Runtime.evaluate', {
      expression: `if (window.__drum && window.__drum.force) { window.__drum.force(${forceOcc}); }`
    });
    await sleep(400);
  }

  const boxRes = await client.send('Runtime.evaluate', {
    expression: `
      (() => {
        const el = document.querySelector('${selector}');
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { x: r.left, y: r.top, width: r.width, height: r.height };
      })()
    `,
    returnByValue: true
  });

  const box = boxRes.result.value;
  if (!box) throw new Error(`未找到元素: ${selector}`);

  const shot = await client.send('Page.captureScreenshot', {
    format: 'png',
    clip: {
      x: box.x,
      y: box.y,
      width: box.width,
      height: box.height,
      scale: 1
    }
  });

  fs.mkdirSync(REVIEW_DIR, { recursive: true });
  const outPath = path.join(REVIEW_DIR, outName);
  fs.writeFileSync(outPath, Buffer.from(shot.data, 'base64'));
  const size = fs.statSync(outPath).size;
  console.log(`✅ 已保存: ${outPath} (${(size / 1024).toFixed(1)} KB)`);
}

async function main() {
  const target = process.argv[2] || 'all';

  await runSession(async (client) => {
    // 1) 桌面整页（1440，DPR=2）
    if (target === 'desktop' || target === 'all') {
      await captureViewport(client, {
        width: 1440,
        height: 900,
        dpr: 2,
        mobile: false,
        outName: 'desktop.png',
        forceOcc: 0.661
      });
    }

    // 2) 鼓特写（DPR=1 与 DPR=2 对比不发虚）
    if (target === 'drum' || target === 'all') {
      await captureElement(client, {
        width: 1440,
        height: 900,
        dpr: 1,
        selector: '.drum-wrap',
        outName: 'drum-1x.png',
        forceOcc: 0.661
      });
      await captureElement(client, {
        width: 1440,
        height: 900,
        dpr: 2,
        selector: '.drum-wrap',
        outName: 'drum-closeup.png',
        forceOcc: 0.661
      });
    }

    // 3) 移动端整页（390，DPR=2）
    if (target === 'mobile' || target === 'all') {
      await captureViewport(client, {
        width: 390,
        height: 844,
        dpr: 2,
        mobile: true,
        outName: 'mobile.png',
        forceOcc: 0.661
      });
      
      // 4) 移动端操作后（勾选 + 触发单条清理行回执）
      await captureViewport(client, {
        width: 390,
        height: 844,
        dpr: 2,
        mobile: true,
        outName: 'mobile-operated.png',
        forceOcc: 0.661,
        onReady: async (c) => {
          await c.send('Runtime.evaluate', {
            expression: `
              (async () => {
                const act = document.querySelector('button.act[data-clean]');
                if (act) {
                  act.click();
                  const yes = act.closest('.card').querySelector('[data-yes]');
                  if (yes) yes.click();
                }
              })()
            `
          });
          await sleep(1000);
        }
      });
    }
  });
}

main().catch(err => {
  console.error('❌ 截图失败:', err);
  process.exit(1);
});
