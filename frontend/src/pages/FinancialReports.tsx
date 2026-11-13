import React, { useEffect, useState } from 'react';
import { Card, Tabs, Table, Button, Space, Select, Tag, Input, message } from 'antd';
import { FileExcelOutlined, CheckCircleOutlined, WarningOutlined, SyncOutlined } from '@ant-design/icons';
import { api } from '../api';

const fmtNum = (v: number | null | undefined) => {
  if (v === null || v === undefined) return '';
  return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

export const FinancialReports: React.FC<{
  accountSetId: string;
  defaultPeriod: string;
  accounts: any[];
  refreshKey: number;
}> = ({ accountSetId, defaultPeriod, accounts, refreshKey }) => {
  const [period, setPeriod] = useState(defaultPeriod);
  const [startPeriod, setStartPeriod] = useState(defaultPeriod);
  const [endPeriod, setEndPeriod] = useState(defaultPeriod);
  const [detailCode, setDetailCode] = useState('1002');
  const [activeReport, setActiveReport] = useState('balance_sheet');

  const [bsData, setBsData] = useState<any>(null);
  const [pnlData, setPnlData] = useState<any>(null);
  const [catData, setCatData] = useState<any>(null);
  const [subData, setSubData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const loadAllReports = async () => {
    setLoading(true);
    try {
      const [bsRes, pnlRes, catRes, subRes] = await Promise.all([
        api.get('/reports/balance-sheet', { params: { account_set_id: accountSetId, period } }),
        api.get('/reports/income-statement', { params: { account_set_id: accountSetId, period } }),
        api.get('/reports/category-summary', { params: { account_set_id: accountSetId, period } }),
        api.get('/reports/subsidiary-ledger', {
          params: {
            account_set_id: accountSetId,
            account_code: detailCode,
            start_period: startPeriod,
            end_period: endPeriod,
          },
        }),
      ]);
      setBsData(bsRes.data);
      setPnlData(pnlRes.data);
      setCatData(catRes.data);
      setSubData(subRes.data);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '加载财务报表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAllReports();
  }, [accountSetId, period, startPeriod, endPeriod, detailCode, refreshKey]);

  const handleExportExcel = async () => {
    try {
      message.loading({ content: '正在生成四合一标准财务报表工作簿...', key: 'exp_fin' });
      const res = await api.get('/reports/export-financial-excel', {
        params: { account_set_id: accountSetId, period, detail_code: detailCode },
        responseType: 'blob',
      });
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `博士特规范财务报表_${period}.xlsx`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      message.success({ content: `已导出《博士特规范财务报表_${period}.xlsx》`, key: 'exp_fin' });
    } catch (err) {
      message.error({ content: '导出 Excel 失败', key: 'exp_fin' });
    }
  };

  return (
    <Card
      title={
        <Space>
          <span>财务规范报表中心（《企业会计制度》2001 / 2024+ 博士特规范）</span>
          {bsData?.check && (
            bsData.check.is_balanced ? (
              <Tag icon={<CheckCircleOutlined />} color="success">
                资产负债表试算平衡（资产 = 负债 + 权益：{fmtNum(bsData.check.total_assets_close)}）
              </Tag>
            ) : (
              <Tag icon={<WarningOutlined />} color="error">
                资产负债表存在差额（资产 {fmtNum(bsData.check.total_assets_close)} vs 权益负债 {fmtNum(bsData.check.total_liab_eq_close)}）
              </Tag>
            )
          )}
        </Space>
      }
      extra={
        <Space>
          <span>报表期间：</span>
          <Input
            size="small"
            style={{ width: 95 }}
            value={period}
            onChange={(e) => {
              setPeriod(e.target.value);
              setStartPeriod(e.target.value);
              setEndPeriod(e.target.value);
            }}
            placeholder="YYYY-MM"
          />
          <Button icon={<SyncOutlined />} size="small" onClick={loadAllReports}>
            刷新
          </Button>
          <Button type="primary" icon={<FileExcelOutlined />} onClick={handleExportExcel}>
            导出四合一规范财务报表 (.xlsx)
          </Button>
        </Space>
      }
    >
      <Tabs
        activeKey={activeReport}
        onChange={setActiveReport}
        items={[
          {
            key: 'balance_sheet',
            label: '资产负债表',
            children: (
              <Table
                rowKey="line_no"
                loading={loading}
                dataSource={bsData?.rows || []}
                pagination={false}
                bordered
                size="small"
                columns={[
                  {
                    title: '资产',
                    dataIndex: 'asset_item',
                    width: 220,
                    render: (text, r) => <span style={{ fontWeight: r.asset_bold ? 'bold' : 'normal' }}>{text}</span>,
                  },
                  { title: '行次', dataIndex: 'line_no', width: 55, align: 'center' },
                  {
                    title: '年初数',
                    dataIndex: 'asset_open',
                    width: 145,
                    align: 'right',
                    render: (v, r) => <span style={{ fontWeight: r.asset_bold ? 'bold' : 'normal' }}>{fmtNum(v)}</span>,
                  },
                  {
                    title: '期末数',
                    dataIndex: 'asset_close',
                    width: 145,
                    align: 'right',
                    render: (v, r) => <span style={{ fontWeight: r.asset_bold ? 'bold' : 'normal' }}>{fmtNum(v)}</span>,
                  },
                  {
                    title: '负债和所有者权益',
                    dataIndex: 'liab_item',
                    width: 220,
                    render: (text, r) => <span style={{ fontWeight: r.liab_bold ? 'bold' : 'normal' }}>{text}</span>,
                  },
                  {
                    title: '年初数',
                    dataIndex: 'liab_open',
                    width: 145,
                    align: 'right',
                    render: (v, r) => <span style={{ fontWeight: r.liab_bold ? 'bold' : 'normal' }}>{fmtNum(v)}</span>,
                  },
                  {
                    title: '期末数',
                    dataIndex: 'liab_close',
                    width: 145,
                    align: 'right',
                    render: (v, r) => <span style={{ fontWeight: r.liab_bold ? 'bold' : 'normal' }}>{fmtNum(v)}</span>,
                  },
                ]}
              />
            ),
          },
          {
            key: 'income_statement',
            label: '损益表（利润表）',
            children: (
              <Table
                rowKey="line_no"
                loading={loading}
                dataSource={pnlData?.lines || []}
                pagination={false}
                bordered
                size="small"
                columns={[
                  {
                    title: '项目',
                    dataIndex: 'item',
                    width: 360,
                    render: (t, r) => <span style={{ fontWeight: r.is_bold ? 'bold' : 'normal' }}>{t}</span>,
                  },
                  { title: '行次', dataIndex: 'line_no', width: 70, align: 'center' },
                  {
                    title: '本月数',
                    dataIndex: 'month_amount',
                    width: 180,
                    align: 'right',
                    render: (v, r) => <span style={{ fontWeight: r.is_bold ? 'bold' : 'normal' }}>{fmtNum(v)}</span>,
                  },
                  {
                    title: '本年累计数',
                    dataIndex: 'ytd_amount',
                    width: 180,
                    align: 'right',
                    render: (v, r) => <span style={{ fontWeight: r.is_bold ? 'bold' : 'normal' }}>{fmtNum(v)}</span>,
                  },
                ]}
              />
            ),
          },
          {
            key: 'category_summary',
            label: '分类表（一级科目汇总）',
            children: (
              <Table
                rowKey="code"
                loading={loading}
                dataSource={catData?.rows || []}
                pagination={false}
                bordered
                size="small"
                scroll={{ x: 1180 }}
                columns={[
                  { title: '科目编码', dataIndex: 'code', width: 90, fixed: 'left' },
                  { title: '科目名称', dataIndex: 'name', width: 150, fixed: 'left' },
                  { title: '类别', dataIndex: 'category', width: 70, align: 'center' },
                  { title: '期初借方', dataIndex: 'opening_debit', width: 120, align: 'right', render: fmtNum },
                  { title: '期初贷方', dataIndex: 'opening_credit', width: 120, align: 'right', render: fmtNum },
                  { title: '本期借方', dataIndex: 'period_debit', width: 120, align: 'right', render: fmtNum },
                  { title: '本期贷方', dataIndex: 'period_credit', width: 120, align: 'right', render: fmtNum },
                  { title: '本年累计借方', dataIndex: 'ytd_debit', width: 130, align: 'right', render: fmtNum },
                  { title: '本年累计贷方', dataIndex: 'ytd_credit', width: 130, align: 'right', render: fmtNum },
                  { title: '期末借方', dataIndex: 'closing_debit', width: 120, align: 'right', render: fmtNum },
                  { title: '期末贷方', dataIndex: 'closing_credit', width: 120, align: 'right', render: fmtNum },
                ]}
                summary={() =>
                  catData?.totals ? (
                    <Table.Summary.Row style={{ fontWeight: 'bold', background: '#fafafa' }}>
                      <Table.Summary.Cell index={0}>合计</Table.Summary.Cell>
                      <Table.Summary.Cell index={1}>
                        {catData.totals.is_balanced ? <Tag color="success">平衡</Tag> : <Tag color="error">不平</Tag>}
                      </Table.Summary.Cell>
                      <Table.Summary.Cell index={2} />
                      <Table.Summary.Cell index={3} align="right">{fmtNum(catData.totals.opening_debit)}</Table.Summary.Cell>
                      <Table.Summary.Cell index={4} align="right">{fmtNum(catData.totals.opening_credit)}</Table.Summary.Cell>
                      <Table.Summary.Cell index={5} align="right">{fmtNum(catData.totals.period_debit)}</Table.Summary.Cell>
                      <Table.Summary.Cell index={6} align="right">{fmtNum(catData.totals.period_credit)}</Table.Summary.Cell>
                      <Table.Summary.Cell index={7} align="right">{fmtNum(catData.totals.ytd_debit)}</Table.Summary.Cell>
                      <Table.Summary.Cell index={8} align="right">{fmtNum(catData.totals.ytd_credit)}</Table.Summary.Cell>
                      <Table.Summary.Cell index={9} align="right">{fmtNum(catData.totals.closing_debit)}</Table.Summary.Cell>
                      <Table.Summary.Cell index={10} align="right">{fmtNum(catData.totals.closing_credit)}</Table.Summary.Cell>
                    </Table.Summary.Row>
                  ) : null
                }
              />
            ),
          },
          {
            key: 'subsidiary_ledger',
            label: '明细表（明细分类账）',
            children: (
              <Space direction="vertical" style={{ width: '100%' }} size="middle">
                <Space wrap>
                  <span>查询科目：</span>
                  <Select
                    showSearch
                    optionFilterProp="label"
                    style={{ width: 320 }}
                    value={detailCode}
                    onChange={setDetailCode}
                    options={accounts.map((a) => ({ value: a.code, label: `${a.code} ${a.name}` }))}
                  />
                  <span>起始期间：</span>
                  <Input size="small" style={{ width: 95 }} value={startPeriod} onChange={(e) => setStartPeriod(e.target.value)} />
                  <span>截止期间：</span>
                  <Input size="small" style={{ width: 95 }} value={endPeriod} onChange={(e) => setEndPeriod(e.target.value)} />
                </Space>
                <Table
                  rowKey={(r, idx) => `${r.date}-${r.voucher_no}-${idx}`}
                  loading={loading}
                  dataSource={subData?.rows || []}
                  bordered
                  size="small"
                  pagination={{
                    defaultPageSize: 30,
                    showSizeChanger: true,
                    pageSizeOptions: ['30', '50', '100'],
                    showTotal: (total) => `共 ${total} 条明细记录`,
                  }}
                  columns={[
                    { title: '日期', dataIndex: 'date', width: 110 },
                    { title: '凭证字号', dataIndex: 'voucher_no', width: 95 },
                    { title: '科目编码', dataIndex: 'account_code', width: 120 },
                    { title: '摘要', dataIndex: 'summary', width: 320 },
                    {
                      title: '借方金额',
                      dataIndex: 'debit',
                      width: 135,
                      align: 'right',
                      render: (v) => (Number(v) !== 0 ? fmtNum(v) : ''),
                    },
                    {
                      title: '贷方金额',
                      dataIndex: 'credit',
                      width: 135,
                      align: 'right',
                      render: (v) => (Number(v) !== 0 ? fmtNum(v) : ''),
                    },
                    { title: '方向', dataIndex: 'direction', width: 65, align: 'center' },
                    {
                      title: '余额',
                      dataIndex: 'balance',
                      width: 145,
                      align: 'right',
                      render: (v) => <strong>{fmtNum(v)}</strong>,
                    },
                  ]}
                />
              </Space>
            ),
          },
        ]}
      />
    </Card>
  );
};
