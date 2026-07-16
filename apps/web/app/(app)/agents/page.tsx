"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Bot, MessageCircle, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api";
import type { Agent } from "@/lib/types";
import { useApp } from "@/components/features/app-shell";
import { PageHeader } from "@/components/features/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";

const EXPERTS = [
  { avatar: "🧪", name: "烯烃研究员", tags: ["乙烯", "丙烯", "聚烯烃"], prompt: "你是烯烃与聚烯烃产业研究员，重点分析裂解成本、装置、库存、基差和下游需求。结论必须引用可核验数据，并区分事实、推断与建议。" },
  { avatar: "⚗️", name: "芳烃研究员", tags: ["PX", "PTA", "聚酯"], prompt: "你是芳烃与聚酯产业链研究员，擅长PX-PTA-聚酯价差、装置动态、加工费与终端排产分析。" },
  { avatar: "📈", name: "期现策略助手", tags: ["基差", "套保", "期权"], prompt: "你是服务化工企业的期现风险管理助手，提供基差、套保比率和情景压力测试；所有建议都要陈述风险边界。" },
  { avatar: "🛡️", name: "危化合规助手", tags: ["GHS", "REACH", "海关"], prompt: "你是危化品合规研究助手，回答必须标出适用地区、法规时间与信息来源；不确定时明确提示人工复核。" },
  { avatar: "🚢", name: "跨境贸易助手", tags: ["询盘", "关税", "物流"], prompt: "你是化工品跨境贸易助手，协助询盘回复、贸易条款、关税成本和物流风险分析，输出可执行清单。" },
  { avatar: "🏭", name: "园区尽调助手", tags: ["园区", "工艺", "招商"], prompt: "你是化工园区与项目尽调助手，从工艺、原料、能耗、安环、物流、政策和市场六个维度生成结构化尽调。" },
] as const;

export default function AgentsPage() {
  const router = useRouter();
  const { agent: activeAgent, replaceAgent } = useApp();
  const [agents, setAgents] = React.useState<Agent[] | null>(null);
  const [createOpen, setCreateOpen] = React.useState(false);
  const [draft, setDraft] = React.useState({ name: "", avatar: "🦞", prompt: "" });
  const [saving, setSaving] = React.useState(false);

  const load = React.useCallback(() => {
    api.listAgents().then(setAgents).catch((error) => toast.error(error instanceof ApiError ? error.message : "龙虾列表加载失败"));
  }, []);
  React.useEffect(load, [load]);

  const openTemplate = (template?: (typeof EXPERTS)[number]) => {
    setDraft(template
      ? { name: template.name, avatar: template.avatar, prompt: template.prompt }
      : { name: "", avatar: "🦞", prompt: "" });
    setCreateOpen(true);
  };

  async function createAgent() {
    if (!draft.name.trim()) return;
    setSaving(true);
    try {
      const created = await api.createAgent({
        name: draft.name.trim(),
        avatar: draft.avatar.trim() || "🦞",
        persona: { system_prompt: draft.prompt.trim(), greeting: `你好，我是${draft.name.trim()}。` },
      });
      setAgents((current) => [...(current ?? []), created]);
      setCreateOpen(false);
      toast.success("专属龙虾已创建");
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "创建失败");
    } finally {
      setSaving(false);
    }
  }

  async function activate(item: Agent) {
    replaceAgent(item);
    router.push("/chat");
    toast.success(`已切换到 ${item.name}`);
  }

  async function remove(item: Agent) {
    if (item.is_default || !window.confirm(`确认删除“${item.name}”及其会话吗？`)) return;
    try {
      await api.deleteAgent(item.id);
      setAgents((current) => current?.filter((value) => value.id !== item.id) ?? []);
      toast.success("已删除");
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "删除失败");
    }
  }

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 p-4 md:p-6">
      <PageHeader
        title="龙虾中心"
        description="为不同化工业务线创建专属 Agent，独立配置人设、知识、技能与会话。"
        actions={<Button onClick={() => openTemplate()}><Plus className="size-4" />新建专属龙虾</Button>}
      />

      <section>
        <div className="mb-3">
          <h2 className="text-sm font-semibold">我的龙虾</h2>
          <p className="mt-1 text-xs text-muted-foreground">当前对话使用的龙虾会高亮显示，切换后会载入它自己的会话。</p>
        </div>
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {agents === null ? Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-48 rounded-xl" />) : agents.map((item) => (
            <Card key={item.id} className={item.id === activeAgent?.id ? "border-primary shadow-lift" : "shadow-soft"}>
              <CardHeader className="pb-3">
                <div className="flex items-start gap-3">
                  <div className="grid size-11 place-items-center rounded-xl bg-primary/10 text-xl">{item.avatar}</div>
                  <div className="min-w-0 flex-1">
                    <CardTitle className="truncate text-base">{item.name}</CardTitle>
                    <CardDescription className="mt-1 line-clamp-2 min-h-9">{item.persona.greeting || "专属化工智能助手"}</CardDescription>
                  </div>
                  {item.id === activeAgent?.id && <Badge>使用中</Badge>}
                </div>
              </CardHeader>
              <CardContent className="flex gap-2">
                <Button className="flex-1" onClick={() => void activate(item)}><MessageCircle className="size-4" />开始畅谈</Button>
                {!item.is_default && <Button variant="outline" size="icon" aria-label="删除" onClick={() => void remove(item)}><Trash2 className="size-4" /></Button>}
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <section>
        <div className="mb-3">
          <h2 className="text-sm font-semibold">专家龙虾</h2>
          <p className="mt-1 text-xs text-muted-foreground">选择化工场景模板，创建后仍可在设置中调整人设和知识连接。</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {EXPERTS.map((expert) => (
            <button key={expert.name} type="button" onClick={() => openTemplate(expert)} className="group rounded-xl border bg-card p-4 text-left shadow-soft transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-lift">
              <div className="flex items-center gap-3"><span className="text-2xl">{expert.avatar}</span><span className="font-medium">{expert.name}</span></div>
              <div className="mt-3 flex flex-wrap gap-1.5">{expert.tags.map((tag) => <Badge key={tag} variant="secondary">{tag}</Badge>)}</div>
            </button>
          ))}
        </div>
      </section>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader><DialogTitle>创建专属龙虾</DialogTitle><DialogDescription>定义头像、名称和专业职责，创建后即可独立对话。</DialogDescription></DialogHeader>
          <div className="grid grid-cols-[5rem_1fr] gap-3"><Input value={draft.avatar} onChange={(event) => setDraft((value) => ({ ...value, avatar: event.target.value }))} className="text-center text-lg" /><Input value={draft.name} onChange={(event) => setDraft((value) => ({ ...value, name: event.target.value }))} placeholder="龙虾名称" /></div>
          <Textarea rows={8} value={draft.prompt} onChange={(event) => setDraft((value) => ({ ...value, prompt: event.target.value }))} placeholder="职责、研究范围、输出要求与边界" />
          <Button onClick={() => void createAgent()} disabled={saving || !draft.name.trim()}><Bot className="size-4" />{saving ? "正在创建…" : "立即创建"}</Button>
        </DialogContent>
      </Dialog>
    </div>
  );
}
