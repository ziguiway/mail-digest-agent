import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('compatible sync is successful and UIDNEXT stays in collapsed diagnostics', async ({
  page,
}) => {
  await page.route('**/api/v1/accounts', async (route) => {
    const response = await route.fetch();
    const accounts = await response.json();
    await route.fulfill({
      response,
      json: accounts.map((account: Record<string, unknown>) =>
        account.id === 'demo_163'
          ? {
              ...account,
              error: null,
              sync_complete: true,
              sync_method: 'uid_inventory',
              sync_diagnostics: ['服务端未提供 UIDNEXT，使用完整邮件编号清单核对同步前后状态'],
            }
          : account,
      ),
    });
  });
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await page.goto('/connections');
  const account = page.locator('.account-card').filter({ hasText: '@163.com' });
  await expect(account.getByText('已同步，使用兼容同步方式')).toBeVisible();
  const note = account.getByText('服务端未提供 UIDNEXT，使用完整邮件编号清单核对同步前后状态');
  await expect(note).not.toBeVisible();
  await account.getByText('同步诊断', { exact: true }).click();
  await expect(note).toBeVisible();
  await page.screenshot({ path: 'test-results/compatible-sync-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('sample workspace has actual mail, digest sources, and a multi-turn answer', async ({
  page,
}) => {
  const failures: string[] = [];
  page.on('pageerror', (error) => failures.push(error.message));
  await page.goto('/');
  await expect(page.getByText('模型回答由规则模拟。', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await expect(page.getByRole('heading', { name: '让重要的来信，先被看见。' })).toBeVisible();
  await page.getByRole('link', { name: '来信摘要', exact: true }).click();
  await expect(page.locator('.digest-item')).toHaveCount(5);
  await page.getByRole('button', { name: '查看邮件依据' }).first().click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.locator('.mail-body')).not.toBeEmpty();
  await page.keyboard.press('Escape');
  await page.getByRole('link', { name: '邮件', exact: true }).click();
  await expect(page.locator('.mail-row')).toHaveCount(6);
  await page.getByLabel('搜索邮件').fill('会议');
  await expect(page.locator('.mail-row')).toHaveCount(2);
  await page.locator('.mail-row').filter({ hasText: '课题组会议时间更新' }).click();
  await expect(page.getByRole('dialog')).toContainText('最近观察到');
  await page.getByRole('button', { name: /课题组会议通知/ }).click();
  await expect(page.locator('.mail-body')).toContainText('A301');
  await page.getByRole('button', { name: '返回最初邮件' }).click();
  await expect(page.locator('.mail-body')).toContainText('B202');
  await page.screenshot({ path: 'test-results/mail-thread.png', fullPage: true });
  await page.keyboard.press('Escape');
  await page.getByRole('link', { name: '智能查询', exact: true }).click();
  await page.getByLabel('邮件问题').fill('找一下导师的会议通知。');
  await page.getByRole('button', { name: '发送问题' }).click();
  await expect(page.locator('.chat-message.assistant')).toHaveCount(1);
  await expect(page.locator('.source-list button').first()).toBeVisible();
  await page.getByLabel('邮件问题').fill('那封邮件后来有更新吗？最终时间是什么？');
  await page.getByRole('button', { name: '发送问题' }).click();
  await expect(page.locator('.chat-message.assistant')).toHaveCount(2);
  await expect(page.locator('.chat-message.assistant').last()).toContainText('16:00');
  await page.reload();
  await expect(page.locator('.chat-message.assistant')).toHaveCount(2);
  await page.locator('.source-list button').last().click();
  await expect(page.getByRole('dialog')).toContainText('B202');
  await page.keyboard.press('Escape');
  await page.screenshot({ path: 'test-results/chat-desktop.png', fullPage: true });
  expect(failures).toEqual([]);
});

test('mobile navigation and refresh retain the session and sources', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await expect(page.getByRole('heading', { name: '让重要的来信，先被看见。' })).toBeVisible();
  await page.getByRole('button', { name: '打开导航' }).click();
  await page.getByRole('link', { name: '邮件', exact: true }).click();
  await expect(page.locator('.mail-row')).toHaveCount(6);
  await page.reload();
  await expect(page.locator('.mail-row')).toHaveCount(6);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: 'test-results/mail-mobile.png', fullPage: true });
  await page.goto('/connections');
  await expect(page.locator('.account-card')).toHaveCount(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('settings support read-only account details and resuming the setup wizard', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await page.getByRole('link', { name: '连接与设置', exact: true }).click();
  await expect(page.locator('.account-card')).toHaveCount(2);
  await page.getByRole('button', { name: '编辑', exact: true }).first().click();
  await expect(page.getByLabel('邮箱地址', { exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await page.getByRole('link', { name: '重新打开配置向导' }).click();
  await expect(page.locator('.setup-main')).toBeVisible();
  await page.getByRole('button', { name: '上一步', exact: true }).click();
  await expect(page.locator('.setup-heading')).toContainText('生成首份摘要');
  await page.reload();
  await expect(page.locator('.setup-heading')).toContainText('生成首份摘要');
});

test('model drafts survive refresh and never persist the API key', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await page.getByRole('link', { name: '连接与设置', exact: true }).click();
  await page.getByRole('button', { name: '模型连接', exact: true }).click();
  await page.getByLabel('模型名称', { exact: true }).fill('draft-model');
  await page.getByLabel('模型 API Key', { exact: true }).fill('draft-secret-not-for-storage');
  await page.reload();
  await page.getByRole('button', { name: '模型连接', exact: true }).click();
  await expect(page.getByLabel('模型名称', { exact: true })).toHaveValue('draft-model');
  await expect(page.getByLabel('模型 API Key', { exact: true })).toBeEmpty();
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain(
    'draft-secret-not-for-storage',
  );
});

test('key screens meet automated accessibility checks', async ({ page }) => {
  await page.goto('/');
  expect(
    (await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()).violations,
  ).toEqual([]);
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await expect(page.getByRole('heading', { name: '让重要的来信，先被看见。' })).toBeVisible();
  for (const route of ['/', '/mail', '/digests', '/chat', '/connections', '/setup']) {
    await page.goto(route);
    await expect(page.locator('.page-content')).not.toBeEmpty();
    await expect(page.locator('.loading')).toHaveCount(0);
    const report = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
    expect(report.violations, `${route} accessibility`).toEqual([]);
  }
});

test('mail state filters use observed membership rather than hiding old content', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await page.getByRole('link', { name: '邮件', exact: true }).click();
  await expect(page.locator('.mail-row')).toHaveCount(6);
  await page.getByLabel('缓存状态', { exact: true }).selectOption('current');
  await expect(page.locator('.mail-row')).toHaveCount(6);
  await expect(page.locator('.mail-row .cache-state').first()).toHaveText('最近观察到');
  await page.getByLabel('缓存状态', { exact: true }).selectOption('historical');
  await expect(page.locator('.mail-row')).toHaveCount(0);
  await expect(page.getByText('当前没有匹配邮件', { exact: true })).toBeVisible();
  await page.getByLabel('缓存状态', { exact: true }).selectOption('unknown');
  await expect(page.locator('.mail-row')).toHaveCount(0);
  await page.getByLabel('缓存状态', { exact: true }).selectOption('all');
  await expect(page.locator('.mail-row')).toHaveCount(6);
});

test('folder discovery retries errors and preserves exact Chinese selection', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await page.getByRole('link', { name: '连接与设置', exact: true }).click();
  await page.getByRole('button', { name: '编辑', exact: true }).first().click();
  await page.getByText('查看服务器与文件夹设置', { exact: true }).click();
  const endpoint = '**/api/v1/accounts/demo_qq/folders';
  await page.route(endpoint, (route) =>
    route.fulfill({ status: 503, json: { detail: '文件夹暂时读取失败，请重试' } }),
  );
  await page.getByRole('button', { name: '读取邮箱文件夹', exact: true }).click();
  await expect(page.getByText('文件夹暂时读取失败，请重试', { exact: true })).toBeVisible();
  await page.unroute(endpoint);
  await page.getByRole('button', { name: '读取邮箱文件夹', exact: true }).click();
  const selector = page.getByRole('combobox', { name: '同步文件夹', exact: true });
  await expect(selector).toBeVisible();
  await expect(selector.locator('option[value="归档"]')).toBeDisabled();
  await selector.selectOption('课程与项目');
  await expect(selector).toHaveValue('课程与项目');
  await expect(page.getByRole('button', { name: '保存并测试连接', exact: true })).toBeInViewport();
  await page.screenshot({ path: 'test-results/folder-discovery.png', fullPage: true });
  await page.getByLabel('SSL 端口', { exact: true }).fill('1993');
  await expect(page.getByRole('button', { name: '读取邮箱文件夹', exact: true })).toBeDisabled();
  await page.getByLabel('SSL 端口', { exact: true }).fill('993');
  await selector.selectOption('INBOX');
  await page.getByRole('button', { name: '取消', exact: true }).click();
});

test('removing a sample connection requires confirmation and leaves other accounts', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByRole('button', { name: '进入演示工作台' }).click();
  await page.getByRole('link', { name: '连接与设置', exact: true }).click();
  await page.getByRole('button', { name: '添加邮箱', exact: true }).click();
  await page.getByLabel('邮箱显示名称', { exact: true }).fill('移除流程检查');
  await page.getByLabel('邮箱地址', { exact: true }).fill('connection-test@qq.com');
  await page.getByRole('button', { name: '保存并测试连接', exact: true }).click();
  const card = page.locator('.account-card').filter({ hasText: '移除流程检查' });
  await expect(card).toBeVisible();
  await card.getByRole('button', { name: '移除连接', exact: true }).click();
  await expect(page.getByRole('dialog')).toContainText('connection-test@qq.com');
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await expect(card).toBeVisible();
  await card.getByRole('button', { name: '移除连接', exact: true }).click();
  await page.getByRole('button', { name: '确认移除连接', exact: true }).click();
  await expect(card).toHaveCount(0);
  await expect(page.locator('.account-card')).toHaveCount(2);
  await expect(
    page.getByText('连接已移除，历史邮件、摘要和来源仍保留。', { exact: true }),
  ).toBeVisible();
});
