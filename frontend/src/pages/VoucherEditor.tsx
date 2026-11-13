import React, { useState } from 'react';
import { Card, Table, Input, Select, InputNumber, Button, DatePicker, Space, message, Tag, Divider } from 'antd';
import { PlusOutlined, DeleteOutlined, SaveOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { api } from '../api';

interface EntryRow {
  key: string;
  summary: string;
  account_code: string;
  debit_amount: number;
  credit_amount: number;
}

export const VoucherEditor: React.FC<{
  accountSetId: string;
  period: string;
  accounts: any[];
  onSaved: () => void;
}> = ({ accountSetId, period, accounts, onSaved }) => {
  const [voucherWord, setVoucherWord] = useState('记');
  const [voucherDate, setVoucherDate] = useState(dayjs('2026-11-13'));
  const [rows, setRows] = useState<EntryRow[]>([
    { key: '1', summary: '', account_code: '', debit_amount: 0, credit_amount: 0 },
    { key: '2', summary: '', account_code: '', debit_amount: 0, credit_amount: 0 },
  ]);
  const [saving, setSaving] = useState(false);

  const leafAccounts = accounts.filter((a) => a.is_leaf);
  const totalDebit = rows.reduce((s, r) => s + Number(r.debit_amount || 0), 0);
  const totalCredit = rows.reduce((s, r) => s + Number(r.credit_amount || 0), 0);

  const updateRow = (idx: number, field: keyof EntryRow, val: any) => {
    const next = [...rows];
    next[idx] = { ...next[idx], [field]: val };
    if (field === 'debit_amount' && Number(val) > 0) next[idx].credit_amount = 0;
    if (field === 'credit_amount' && Number(val) > 0) next[idx].debit_amount = 0;
    setRows(next);
  };

  const handleAmountKeyDown = (e: React.KeyboardEvent, idx: number) => {
    if (e.key === '=') {
      e.preventDefault();
      const otherDebit = rows.reduce((s, r, i) => (i === idx ? s : s + Number(r.debit_amount || 0)), 0);
      const otherCredit = rows.reduce((s, r, i) => (i === idx ? s : s + Number(r.credit_amount || 0)), 0);
      const diff = Number((otherCredit - otherDebit).toFixed(2));
      const next = [...rows];
      if (diff > 0) {
        next[idx].debit_amount = diff;
        next[idx].credit_amount = 0;
      } else if (diff < 0) {
        next[idx].credit_amount = Math.abs(diff);
        next[idx].debit_amount = 0;
      }
      setRows(next);
    } else if (e.key === ' ') {
      e.preventDefault();
      const next = [...rows];
      const d = next[idx].debit_amount;
      const c = next[idx].credit_amount;
      next[idx].debit_amount = c;
      next[idx].credit_amount = d;
      setRows(next);
    } else if (e.key === 'Enter' && idx === rows.length - 1) {
      setRows([...rows, { key: String(Date.now()), summary: rows[idx].summary, account_code: '', debit_amount: 0, credit_amount: 0 }]);
    }
  };

  const handleSave = async () => {
    const validEntries = rows.filter((r) => r.account_code && (r.debit_amount > 0 || r.credit_amount > 0));
    if (validEntries.length < 2) {
      message.warning('请至少录入两条有效分录');
      return;
    }
    if (Math.abs(totalDebit - totalCredit) > 0.001) {
      message.error(`借贷不平衡！借方合计 ${totalDebit.toFixed(2)}，贷方合计 ${totalCredit.toFixed(2)}`);
      return;
    }
    setSaving(true);
    try {
      const res = await api.post('/vouchers', {
        account_set_id: accountSetId,
        period,
        voucher_word: voucherWord,
        voucher_date: voucherDate.format('YYYY-MM-DD'),
        entries: validEntries.map((e) => ({
          summary: e.summary || '日常业务记账',
          account_code: e.account_code,
          debit_amount: e.debit_amount,
          credit_amount: e.credit_amount,
        })),
      });
      message.success(`凭证 [${res.data.voucher_word}-${res.data.voucher_no}] 保存并过账成功！`);
      setRows([
        { key: String(Date.now() + 1), summary: '', account_code: '', debit_amount: 0, credit_amount: 0 },
        { key: String(Date.now() + 2), summary: '', account_code: '', debit_amount: 0, credit_amount: 0 },
      ]);
      onSaved();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      title={
        <Space>
          <span>记账凭证录入</span>
          <Tag color="blue">快捷键提示：金额框按 "=" 一键找平借贷差额 | 按 "空格" 切换借贷方向</Tag>
        </Space>
      }
      extra={
        <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>
          保存并新增
        </Button>
      }
    >
      <Space style={{ marginBottom: 16 }}>
        <span>凭证字：</span>
        <Select value={voucherWord} onChange={setVoucherWord} style={{ width: 80 }} options={['记', '收', '付', '转'].map((w) => ({ label: w, value: w }))} />
        <span>日期：</span>
        <DatePicker value={voucherDate} onChange={(d) => d && setVoucherDate(d)} />
        <span>会计期间：</span>
        <Tag color="geekblue">{period}</Tag>
      </Space>

      <Table
        dataSource={rows}
        pagination={false}
        bordered
        size="small"
        columns={[
          {
            title: '摘要',
            dataIndex: 'summary',
            width: '28%',
            render: (val, _, idx) => (
              <Input
                value={val}
                placeholder="输入摘要..."
                onChange={(e) => updateRow(idx, 'summary', e.target.value)}
              />
            ),
          },
          {
            title: '会计科目（支持编码/名称/拼音首字母）',
            dataIndex: 'account_code',
            width: '34%',
            render: (val, _, idx) => (
              <Select
                showSearch
                value={val || undefined}
                placeholder="如: 100201 / 银行 / YHCK"
                style={{ width: '100%' }}
                filterOption={(input, option) =>
                  (option?.searchText ?? '').toLowerCase().includes(input.toLowerCase())
                }
                onChange={(v) => updateRow(idx, 'account_code', v)}
                options={leafAccounts.map((a) => ({
                  value: a.code,
                  label: `${a.code} ${a.name}`,
                  searchText: `${a.code} ${a.name} ${a.pinyin_abbr}`,
                }))}
              />
            ),
          },
          {
            title: '借方金额',
            dataIndex: 'debit_amount',
            width: '16%',
            render: (val, _, idx) => (
              <InputNumber
                value={val}
                precision={2}
                style={{ width: '100%' }}
                onChange={(v) => updateRow(idx, 'debit_amount', v || 0)}
                onKeyDown={(e) => handleAmountKeyDown(e, idx)}
              />
            ),
          },
          {
            title: '贷方金额',
            dataIndex: 'credit_amount',
            width: '16%',
            render: (val, _, idx) => (
              <InputNumber
                value={val}
                precision={2}
                style={{ width: '100%' }}
                onChange={(v) => updateRow(idx, 'credit_amount', v || 0)}
                onKeyDown={(e) => handleAmountKeyDown(e, idx)}
              />
            ),
          },
          {
            title: '操作',
            width: '6%',
            render: (_, __, idx) => (
              <Button
                type="text"
                danger
                icon={<DeleteOutlined />}
                disabled={rows.length <= 2}
                onClick={() => setRows(rows.filter((_, i) => i !== idx))}
              />
            ),
          },
        ]}
        summary={() => (
          <Table.Summary.Row style={{ background: '#fafafa', fontWeight: 'bold' }}>
            <Table.Summary.Cell index={0} colSpan={2}>
              <Button
                type="dashed"
                size="small"
                icon={<PlusOutlined />}
                onClick={() =>
                  setRows([...rows, { key: String(Date.now()), summary: '', account_code: '', debit_amount: 0, credit_amount: 0 }])
                }
              >
                增行
              </Button>
              <Divider type="vertical" />
              <span>合计（人民币）：</span>
            </Table.Summary.Cell>
            <Table.Summary.Cell index={2}>{totalDebit.toFixed(2)}</Table.Summary.Cell>
            <Table.Summary.Cell index={3}>{totalCredit.toFixed(2)}</Table.Summary.Cell>
            <Table.Summary.Cell index={4} />
          </Table.Summary.Row>
        )}
      />
    </Card>
  );
};