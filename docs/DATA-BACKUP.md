# ChemClaw 数据备份与恢复

## 完整备份

在项目根目录执行：

```powershell
npm run db:backup
```

命令会短暂停止 API 与 Web 写入，完整打包 Docker 数据卷，并恢复原先正在运行的服务。备份同时包含：

- SQLite 关系数据；
- LanceDB 向量索引；
- 上传文件与结构化导入原文件；
- 引擎配置和项目运行数据。

默认生成：

```text
backups/chemclaw-data.tgz
backups/chemclaw-data-YYYYMMDD-HHMMSS.tgz
```

直接备份到坚果云目录：

```powershell
npm run db:backup -- -BackupDir "D:\坚果云\我的坚果云\芯化和云\ChemClaw备份"
```

## 完整恢复

```powershell
npm run db:restore -- -BackupFile "D:\备份目录\chemclaw-data.tgz"
```

恢复前必须手动输入 `RESTORE`。脚本会先在同一目录生成恢复前安全快照，再校验压缩包并恢复数据。恢复过程不会重新切片、抽取或生成向量。

不要执行 `docker compose down -v`，其中 `-v` 会删除数据卷。

## 旧 PostgreSQL 备份

`sag_lite.dump` 是 PostgreSQL 自定义格式备份，而当前默认运行环境使用 SQLite + LanceDB，因此不能把它直接交给 `db:restore` 覆盖当前数据卷。

可以先做只读检查：

```powershell
npm run db:inspect-legacy -- -BackupFile "D:\坚果云\我的坚果云\芯化和云\2026年6月\SAG-main\backups\sag_lite.dump"
```

旧库应先在隔离的 PostgreSQL 环境中导出项目级 JSON，再通过项目内的“结构化数据中心”导入。导入器会保留旧 ID、来源、切片、事件、实体、关系和未知扩展字段，不重新切片或抽取。
