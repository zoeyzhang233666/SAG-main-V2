"use client";

import { Cable, Network } from "lucide-react";

import { McpServiceSettings } from "@/components/features/mcp-service-settings";
import { McpSettingsCard } from "@/components/features/mcp-settings-card";
import { PageHeader } from "@/components/features/page-header";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

export default function ConnectionsPage() {
  return (
    <div className="mx-auto w-full max-w-4xl space-y-6 p-4 md:p-6">
      <PageHeader title="连接中心" description="把 ChemClaw 连接到外部 MCP 工具，也可将完整知识库作为 MCP 服务提供给其他客户端。" />
      <Alert className="border-primary/20 bg-primary/[0.035]"><Network className="size-4 text-primary" /><AlertTitle>双向连接</AlertTitle><AlertDescription>“外部工具”由当前知识库问答助手调用；“知识库服务”用于让其他 Agent 或桌面客户端检索你的项目。</AlertDescription></Alert>
      <section className="space-y-3"><div className="flex items-center gap-2"><Cable className="size-4 text-primary" /><h2 className="text-sm font-semibold">外部工具与业务系统</h2></div><McpSettingsCard /></section>
      <section className="space-y-3"><div className="flex items-center gap-2"><Network className="size-4 text-primary" /><h2 className="text-sm font-semibold">ChemClaw 知识库服务</h2></div><McpServiceSettings /></section>
    </div>
  );
}
