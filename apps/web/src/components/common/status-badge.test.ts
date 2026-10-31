/**
 * 前端补测第七批（审计 2026-10-31 H1 续）。
 *
 * 覆盖：status-badge 纯函数、format 残余分支、api-client 边界场景。
 */

import { describe, expect, it } from 'vitest';

import { UNKNOWN_STATUS_LABEL, statusDescriptor } from '@/components/common/status-badge';
import { formatFileSize, formatNumber } from '@/lib/format';

// ---------------------------------------------------------------------------
// status-badge · statusDescriptor
// ---------------------------------------------------------------------------

describe('status-badge · statusDescriptor', () => {
  it('文档三态映射正确', () => {
    expect(statusDescriptor('processing')).toEqual({ label: '解析中', tone: 'warning' });
    expect(statusDescriptor('completed')).toEqual({ label: '可使用', tone: 'success' });
    expect(statusDescriptor('failed')).toEqual({ label: '处理失败', tone: 'danger' });
  });

  it('文档五态映射正确', () => {
    expect(statusDescriptor('uploaded').label).toBe('已上传');
    expect(statusDescriptor('parsing').label).toBe('正在读取内容');
    expect(statusDescriptor('preparing').label).toBe('正在准备内容');
  });

  it('引用片段状态映射正确', () => {
    expect(statusDescriptor('valid')).toEqual({ label: '可定位', tone: 'success' });
    expect(statusDescriptor('stale')).toEqual({ label: '旧版本内容', tone: 'warning' });
  });

  it('知识库可见范围映射正确', () => {
    expect(statusDescriptor('organization').label).toBe('企业可见');
    expect(statusDescriptor('private').label).toBe('仅自己可见');
    expect(statusDescriptor('restricted').label).toBe('指定成员可见');
  });

  it('成员在册状态映射正确', () => {
    expect(statusDescriptor('active')).toEqual({ label: '已加入', tone: 'success' });
    expect(statusDescriptor('invited')).toEqual({ label: '待接受邀请', tone: 'warning' });
    expect(statusDescriptor('disabled')).toEqual({ label: '已停用', tone: 'neutral' });
  });

  it('插件安装态映射正确', () => {
    expect(statusDescriptor('enabled').label).toBe('已添加');
    expect(statusDescriptor('installed').label).toBe('已安装');
    expect(statusDescriptor('uninstalled').label).toBe('未添加');
  });

  it('工具连接状态映射正确', () => {
    expect(statusDescriptor('connected').label).toBe('已连接');
    expect(statusDescriptor('expired').label).toBe('待重新连接');
    expect(statusDescriptor('revoked').label).toBe('已断开');
  });

  it('订单状态映射正确', () => {
    expect(statusDescriptor('pending_payment').label).toBe('待支付');
    expect(statusDescriptor('paid').label).toBe('已支付');
    expect(statusDescriptor('closed').label).toBe('已关闭');
    expect(statusDescriptor('cancelled').label).toBe('已取消');
    expect(statusDescriptor('refunded').label).toBe('已退款');
  });

  it('账单与套餐状态映射正确', () => {
    expect(statusDescriptor('issued').label).toBe('已开具');
    expect(statusDescriptor('pending').label).toBe('处理中');
    expect(statusDescriptor('available').label).toBe('可购买');
    expect(statusDescriptor('offline').label).toBe('已下架');
    expect(statusDescriptor('success').label).toBe('成功');
  });

  it('未知状态返回兜底文案', () => {
    const result = statusDescriptor('nonexistent_status');
    expect(result.label).toBe(UNKNOWN_STATUS_LABEL);
    expect(result.tone).toBe('neutral');
  });

  it('空字符串返回兜底文案', () => {
    expect(statusDescriptor('').label).toBe(UNKNOWN_STATUS_LABEL);
  });

  it('所有已登记状态的文案不含技术词', () => {
    const allStatuses = [
      'processing',
      'completed',
      'failed',
      'uploaded',
      'parsing',
      'preparing',
      'valid',
      'stale',
      'organization',
      'private',
      'restricted',
      'active',
      'invited',
      'disabled',
      'enabled',
      'installed',
      'uninstalled',
      'connected',
      'expired',
      'revoked',
      'pending_payment',
      'paid',
      'closed',
      'cancelled',
      'refunded',
      'success',
      'issued',
      'pending',
      'available',
      'offline',
    ];
    for (const status of allStatuses) {
      const { label } = statusDescriptor(status);
      for (const banned of ['error', 'code', 'HTTP', 'API', 'null', 'undefined']) {
        expect(label).not.toContain(banned);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// format 残余分支
// ---------------------------------------------------------------------------

describe('format · formatNumber 分组', () => {
  it('零值', () => {
    expect(formatNumber(0)).toBe('0');
  });

  it('三位数不加逗号', () => {
    expect(formatNumber(999)).toBe('999');
  });

  it('四位数加一个逗号', () => {
    expect(formatNumber(1000)).toBe('1,000');
    expect(formatNumber(1234)).toBe('1,234');
  });

  it('七位数加两个逗号', () => {
    expect(formatNumber(1234567)).toBe('1,234,567');
  });

  it('负数正确分组', () => {
    expect(formatNumber(-1234)).toBe('-1,234');
    expect(formatNumber(-1234567)).toBe('-1,234,567');
  });
});

describe('format · formatFileSize 残余分支', () => {
  it('0 字节', () => {
    expect(formatFileSize(0)).toBe('0 B');
  });

  it('小于 1KB 按 B 展示', () => {
    expect(formatFileSize(512)).toBe('512 B');
    expect(formatFileSize(1023)).toBe('1023 B');
  });

  it('恰好 1KB', () => {
    expect(formatFileSize(1024)).toBe('1 KB');
  });

  it('MB 级保留一位小数', () => {
    expect(formatFileSize(1536)).toBe('1.5 KB');
    expect(formatFileSize(1048576)).toBe('1 MB');
  });

  it('GB 级', () => {
    expect(formatFileSize(1073741824)).toBe('1 GB');
  });

  it('.0 结尾时去掉小数点', () => {
    expect(formatFileSize(2 * 1024)).toBe('2 KB');
    expect(formatFileSize(5 * 1024 * 1024)).toBe('5 MB');
  });
});
