import React, { useEffect, useState } from 'react';
import { Card, Upload, Button, Table, Tag, Select, Switch, Space, message, Tooltip } from 'antd';
import { UploadOutlined, ThunderboltOutlined, RobotOutlined, SyncOutlined } from '@ant-design/icons';
import { api } from '../api';

export const BankStatements: React.FC<{
  accountSetId: string;
  bankAccounts: any[];
  accounts: any[];
  onVoucherGenerated: () => void;
}> = ({ accountSetId, bankAccounts, accounts, onVoucherGenerated }) => {
  const [statements, setStatements] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [autoGenOnImport, setAutoGenOnImport] = useState(false);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [mergeMode, setMergeMode] = useState<'ONE_TO_ONE' | 'MERGE_BY_CATEGORY' | 'MERGE_ALL'>('ONE_TO_ONE');

  const leafAccounts = accounts.filter((a) => a.is_leaf);
  const defaultBankId = bankAccounts[0]?.id;

  const fetchStatements = async () => {
    setLoading(true);
    try {
      const res = await api.get('/statements', { params: { account_set_id: accountSetId } });
      setStatements(res.data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStatements();
  }, [accountSetId]);

  const handleUpload = async (options: any) => {
    const formData = new FormData();
    formData.append('account_set_id', accountSetId);
    formData.append('bank_account_id', defaultBankId);
    formData.append('auto_generate_voucher', String(autoGenOnImport));
    formData.append('file', options.file);

    try {
      message.loading({ content: 'AI Agent 正在解析对账单并标记业务要素...', key: 'imp' });
      const res = await api.post('/statements/import', formData);
      message.success({
        content: `解析入库 ${res.data.parsed_count} 条流水，正向自动生成凭证 ${res.data.generated_vouchers_count} 张！`,
        key: 'imp',
      });
      fetchStatements();
      if (res.data.generated_vouchers_count > 0) onVoucherGenerated();
    } catch (err: any) {
      message.error({ content: err.response?.data?.detail || '解析失败', key: 'imp' });
    }
  };

  const handleSubjectChange = async (statementId: string, newCode: string) => {
    await api.patch(`/statements/${statementId}/tags`, { suggested_account_code: newCode });
    message.success('已更新 AI 标记科目');
    fetchStatements();
  };

  const handleReverseGenerate = async (targetIds?: string[]) => {
    const ids = targetIds || (selectedRowKeys as string[]);
    if (!ids.length) {
      message.warning('请先勾选需要反向生成凭证的对账单流水');
      return;
    }
    try {
      const res = await api.post('/statements/reverse-generate-vouchers', {
        account_set_id: accountSetId,
        statement_ids: ids,
        merge_mode: mergeMode,
        voucher_word: '记',
        overwrite_existing: true,
      });
      message.success(`成功从数据库流水反向生成 ${res.data.generated_count} 张记账凭证！`);
      setSelectedRowKeys([]);
      fetchStatements();
      onVoucherGenerated();
    } catch (err: any) {
      message.error(err.response?.data?.detail || '反向生成失败');
    }
  };

  return (
    <Card
      title={
        <Space>
          <RobotOutlined style={{ color: '#1677ff' }} />
          <span>AI 智能银行对账单与双向凭证中心</span>
        </Space>
      }
      extra={
        <Space wrap>
          <span>导入时立即正向制证：</span>
          <Switch checked={autoGenOnImport} onChange={setAutoGenOnImport} />
          <Upload customRequest={handleUpload} showUploadList={false} accept=".xlsx,.xls,.csv,.pdf">
            <Button type="primary" icon={<UploadOutlined />}>
              上传对账单 (Excel/CSV/PDF)
            </Button>
          </Upload>
          <Select
            value={mergeMode}
            onChange={setMergeMode}
            style={{ width: 170 }}
            options={[
              { value: 'ONE_TO_ONE', label: '反向制证：逐笔一证' },
              { value: 'MERGE_BY_CATEGORY', label: '反向制证：按类别合并' },
              { value: 'MERGE_ALL', label: '反向制证：全部合并一证' },
            ]}
          />
          <Button
            icon={<ThunderboltOutlined />}
            onClick={() => handleReverseGenerate()}
            disabled={!selectedRowKeys.length}
          >
            数据库反向批量制证 ({selectedRowKeys.length})
          </Button>
          <Button icon={<SyncOutlined />} onClick={fetchStatements} />
        </Space>
      }
    >
      <Table
        rowKey="id"
        loading={loading}
        dataSource={statements}
        rowSelection={{ selectedRowKeys, onChange: setSelectedRowKeys }}
        size="small"
        columns={[
          { title: '交易时间', dataIndex: 'tx_date', width: 155 },
          {
            title: '方向',
            dataIndex: 'direction',
            width: 75,
            render: (d) => (d === 'IN' ? <Tag color="green">收入</Tag> : <Tag color="volcano">支出</Tag>),
          },
          {
            title: '发生额',
            dataIndex: 'amount',
            width: 110,
            render: (v) => <strong>{Number(v).toFixed(2)}</strong>,
          },
          { title: '对手方户名 (AI抽取)', dataIndex: 'counterparty_name', width: 180 },
          {
            title: 'AI 业务意图与标准化摘要',
            width: 240,
            render: (_, r) => (
              <Space direction="vertical" size={2}>
                <Space>
                  <Tag color="purple">{r.ai_tags?.intent_category || 'OTHER'}</Tag>
                  <Tag color="cyan">置信度 {(r.confidence_score * 100).toFixed(0)}%</Tag>
                </Space>
                <Tooltip title={`原始摘要: ${r.raw_summary}`}>
                  <span style={{ fontSize: 12 }}>{r.ai_tags?.normalized_summary || r.raw_summary}</span>
                </Tooltip>
              </Space>
            ),
          },
          {
            title: 'AI 标记对方科目（可微调）',
            dataIndex: 'suggested_account_code',
            width: 220,
            render: (code, r) => (
              <Select
                size="small"
                showSearch
                value={code}
                style={{ width: '100%' }}
                onChange={(val) => handleSubjectChange(r.id, val)}
                options={leafAccounts.map((a) => ({ value: a.code, label: `${a.code} ${a.name}` }))}
              />
            ),
          },
          {
            title: '凭证状态 / 双向联动',
            width: 150,
            render: (_, r) =>
              r.voucher_label ? (
                <Space>
                  <Tag color="success">{r.voucher_label}</Tag>
                  <Button type="link" size="small" onClick={() => handleReverseGenerate([r.id])}>
                    重算
                  </Button>
                </Space>
              ) : (
                <Button type="link" size="small" onClick={() => handleReverseGenerate([r.id])}>
                  立即反向制证
                </Button>
              ),
          },
        ]}
      />
    </Card>
  );
};