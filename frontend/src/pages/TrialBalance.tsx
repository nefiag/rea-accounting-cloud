import React, { useEffect, useState } from 'react';
import { Card, Table, Button, Space, Tag, Upload, message } from 'antd';
import { DownloadOutlined, UploadOutlined, SyncOutlined } from '@ant-design/icons';
import { api } from '../api';

export const TrialBalance: React.FC<{
  accountSetId: string;
  period: string;
  refreshKey: number;
  onRefreshBootstrap: () => void;
}> = ({ accountSetId, period, refreshKey, onRefreshBootstrap }) => {
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await api.get('/reports/trial-balance', { params: { account_set_id: accountSetId, period } });
      setData(res.data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [accountSetId, period, refreshKey]);

  const handleImportXkj = async (opt: any) => {
    const fd = new FormData();
    fd.append('account_set_id', accountSetId);
    fd.append('file', opt.file);
    const res = await api.post('/migration/import-xiaokuaiji', fd);
    message.success(`晓会计模板导入成功，新增科目 ${res.data.imported_accounts} 个`);
    loadData();
    onRefreshBootstrap();
  };

  return (
    <Card
      title={`科目余额表（会计期间：${period}）`}
      extra={
        <Space>
          <Upload customRequest={handleImportXkj} showUploadList={false} accept=".xlsx">
            <Button icon={<UploadOutlined />}>导入晓会计 Excel 模板</Button>
          </Upload>
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            href={`/api/v1/migration/export-xiaokuaiji?account_set_id=${accountSetId}&period=${period}`}
          >
            导出晓会计兼容格式 (.xlsx)
          </Button>
          <Button icon={<SyncOutlined />} onClick={loadData} />
        </Space>
      }
    >
      <Table
        rowKey="code"
        loading={loading}
        dataSource={data}
        pagination={false}
        bordered
        size="small"
        columns={[
          { title: '科目编码', dataIndex: 'code', width: 110 },
          {
            title: '科目名称',
            dataIndex: 'name',
            render: (name, r) => <span style={{ paddingLeft: (r.level - 1) * 16 }}>{name}</span>,
          },
          { title: '类别', dataIndex: 'category', width: 80, render: (c) => <Tag>{c}</Tag> },
          { title: '期初借方', dataIndex: 'opening_debit', align: 'right', render: (v) => v.toFixed(2) },
          { title: '期初贷方', dataIndex: 'opening_credit', align: 'right', render: (v) => v.toFixed(2) },
          { title: '本期借方发生', dataIndex: 'period_debit', align: 'right', render: (v) => v.toFixed(2) },
          { title: '本期贷方发生', dataIndex: 'period_credit', align: 'right', render: (v) => v.toFixed(2) },
          {
            title: '期末借方余额',
            dataIndex: 'closing_debit',
            align: 'right',
            render: (v) => <strong>{v.toFixed(2)}</strong>,
          },
          {
            title: '期末贷方余额',
            dataIndex: 'closing_credit',
            align: 'right',
            render: (v) => <strong>{v.toFixed(2)}</strong>,
          },
        ]}
      />
    </Card>
  );
};