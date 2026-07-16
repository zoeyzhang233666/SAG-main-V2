"use client";

import * as React from "react";
import { Check, Search, Sparkles, WandSparkles } from "lucide-react";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api";
import type { Agent } from "@/lib/types";
import { useApp } from "@/components/features/app-shell";
import { PageHeader } from "@/components/features/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const SKILLS = [
  { id: "market-daily", icon: "📊", category: "市场情报", name: "化工品市场日报", description: "汇总现货、期货、基差、装置、库存与下游消费，生成可分发日报。", instruction: "生成市场日报时，按价格与基差、装置与供应、库存与物流、下游需求、风险与策略五部分输出；数据需标明日期和来源。" },
  { id: "spread-radar", icon: "📈", category: "市场情报", name: "基差雷达", description: "识别区域贴水、升水和跨期价差机会，输出点价与套保观察。", instruction: "分析基差时，必须给出现货区域、期货合约、计算口径、历史分位与风险情景，禁止把推断表述为确定收益。" },
  { id: "balance-sheet", icon: "⚖️", category: "产业研究", name: "供需平衡表", description: "按产能、产量、进出口、库存和消费推演月度供需。", instruction: "供需平衡推演使用表格列出产量、进口、出口、显性库存变化与表观消费，并分别给出基准、乐观、悲观情景。" },
  { id: "plant-calendar", icon: "🏭", category: "产业研究", name: "装置检修日历", description: "整理投产、停车、复产和事故，评估边际供给影响。", instruction: "装置分析按企业、地区、产能、工艺、状态、起止时间、可信度整理；重复消息需合并，未确认信息单独标注。" },
  { id: "buyer-discovery", icon: "🎯", category: "商机线索", name: "潜在采购商挖掘", description: "按品种、地区、规模和采购周期筛选潜在客户并生成跟进建议。", instruction: "商机线索必须区分已验证事实和推测画像，给出筛选依据、优先级、联系路径与下一步验证动作，不编造个人联系方式。" },
  { id: "supplier-dd", icon: "🔎", category: "商机线索", name: "供应商尽调", description: "从资质、产能、历史履约、合规和舆情生成结构化尽调。", instruction: "供应商尽调按工商与资质、产能与产品、履约与物流、财务与信用、安环合规、舆情风险六部分输出，并附证据清单。" },
  { id: "compliance", icon: "🛡️", category: "合规法规", name: "合规变更追踪", description: "跟踪危化品目录、GHS、REACH、海关和双碳规则变化。", instruction: "法规回答必须注明司法辖区、发布机构、生效日期和原文链接；涉及申报或法律责任时提示专业人员复核。" },
  { id: "hs-lookup", icon: "🌐", category: "合规法规", name: "关税与 HS 速查", description: "根据产品名或 CAS 梳理编码候选、税率和贸易管制核验清单。", instruction: "HS 编码只能给出候选及判断依据，必须提示以海关归类决定为准；列出CAS、纯度、用途和形态等归类所需信息。" },
  { id: "hypergraph", icon: "🕸️", category: "数据工具", name: "知识超图生成", description: "从化工资料抽取切片、事件、实体和超边关系，输出可导入结构。", instruction: "知识超图输出必须保留原文切片，事件包含标题、摘要、内容、关键词，实体包含类型、名称、说明和关系；使用稳定ID并输出可直接导入的JSON。" },
  { id: "interview", icon: "🎙️", category: "内容创作", name: "化工行业访谈", description: "按产业框架生成专家访谈提纲和追问树。", instruction: "访谈提纲覆盖产能、成本、供需、竞争格局、渠道、政策和未来变量；每个问题附追问方向与可验证指标。" },
] as const;

const CATEGORIES = ["全部", ...Array.from(new Set(SKILLS.map((skill) => skill.category)))] as const;

function stripSkill(prompt: string, id: string) {
  const start = `[CHEMCLAW_SKILL:${id}]`;
  const end = "[/CHEMCLAW_SKILL]";
  const from = prompt.indexOf(start);
  if (from < 0) return prompt;
  const to = prompt.indexOf(end, from);
  return `${prompt.slice(0, from)}${to < 0 ? "" : prompt.slice(to + end.length)}`.trim();
}

export default function SkillsPage() {
  const { agent: activeAgent, replaceAgent } = useApp();
  const [agents, setAgents] = React.useState<Agent[] | null>(null);
  const [agentId, setAgentId] = React.useState(activeAgent?.id ?? "");
  const [category, setCategory] = React.useState<string>("全部");
  const [query, setQuery] = React.useState("");
  const [saving, setSaving] = React.useState("");

  React.useEffect(() => {
    api.listAgents().then((items) => {
      setAgents(items);
      if (!agentId) setAgentId(activeAgent?.id ?? items[0]?.id ?? "");
    }).catch(() => setAgents([]));
  }, [activeAgent?.id, agentId]);

  const selectedAgent = agents?.find((item) => item.id === agentId) ?? activeAgent;
  const installed = new Set(selectedAgent?.persona.skills ?? []);
  const visible = SKILLS.filter((skill) =>
    (category === "全部" || skill.category === category)
    && (!query.trim() || `${skill.name}${skill.description}${skill.category}`.toLowerCase().includes(query.trim().toLowerCase())),
  );

  async function toggleSkill(skill: (typeof SKILLS)[number]) {
    if (!selectedAgent) return;
    setSaving(skill.id);
    try {
      const nextInstalled = installed.has(skill.id)
        ? (selectedAgent.persona.skills ?? []).filter((id) => id !== skill.id)
        : [...(selectedAgent.persona.skills ?? []), skill.id];
      const without = stripSkill(selectedAgent.persona.system_prompt ?? "", skill.id);
      const systemPrompt = installed.has(skill.id)
        ? without
        : `${without}\n\n[CHEMCLAW_SKILL:${skill.id}]\n${skill.instruction}\n[/CHEMCLAW_SKILL]`.trim();
      const updated = await api.updateAgent(selectedAgent.id, {
        persona: { ...selectedAgent.persona, skills: nextInstalled, system_prompt: systemPrompt },
      });
      setAgents((items) => items?.map((item) => item.id === updated.id ? updated : item) ?? []);
      if (updated.id === activeAgent?.id) replaceAgent(updated);
      toast.success(installed.has(skill.id) ? "已移除技能" : "技能已挂载到龙虾");
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "技能配置失败");
    } finally {
      setSaving("");
    }
  }

  return (
    <div className="mx-auto w-full max-w-7xl space-y-5 p-4 md:p-6">
      <PageHeader title="技能广场" description="把化工研究方法挂载到指定龙虾；技能会真实写入 Agent 人设，并在后续任务中持续生效。" />
      <div className="flex flex-col gap-3 rounded-xl border bg-card p-3 shadow-soft md:flex-row md:items-center">
        <Select value={agentId} onValueChange={setAgentId}>
          <SelectTrigger className="w-full md:w-64"><SelectValue placeholder="选择龙虾" /></SelectTrigger>
          <SelectContent>{agents?.map((item) => <SelectItem key={item.id} value={item.id}>{item.avatar} {item.name}</SelectItem>)}</SelectContent>
        </Select>
        <div className="relative flex-1"><Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" /><Input value={query} onChange={(event) => setQuery(event.target.value)} className="pl-9" placeholder="搜索技能" /></div>
      </div>
      <div className="flex flex-wrap gap-2">{CATEGORIES.map((item) => <Button key={item} variant={category === item ? "default" : "outline"} size="sm" onClick={() => setCategory(item)}>{item}</Button>)}</div>
      {agents === null ? <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{Array.from({ length: 6 }).map((_, index) => <Skeleton key={index} className="h-52 rounded-xl" />)}</div> : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {visible.map((skill) => {
            const active = installed.has(skill.id);
            return (
              <article key={skill.id} className={cn("flex min-h-52 flex-col rounded-xl border bg-card p-4 shadow-soft transition-all hover:-translate-y-0.5 hover:shadow-lift", active && "border-primary/45")}>
                <div className="flex items-start justify-between"><div className="grid size-10 place-items-center rounded-xl bg-primary/10 text-xl">{skill.icon}</div>{active && <Badge><Check className="mr-1 size-3" />已挂载</Badge>}</div>
                <h2 className="mt-3 text-sm font-semibold">{skill.name}</h2>
                <p className="mt-1.5 flex-1 text-xs leading-5 text-muted-foreground">{skill.description}</p>
                <div className="mt-3 flex items-center justify-between"><Badge variant="secondary">{skill.category}</Badge><Button size="sm" variant={active ? "outline" : "default"} disabled={!selectedAgent || saving === skill.id} onClick={() => void toggleSkill(skill)}><WandSparkles className="size-3.5" />{saving === skill.id ? "保存中…" : active ? "移除" : "挂载技能"}</Button></div>
              </article>
            );
          })}
        </div>
      )}
      <div className="rounded-xl border border-primary/20 bg-primary/[0.035] p-4 text-xs leading-5 text-muted-foreground"><Sparkles className="mr-2 inline size-4 text-primary" />技能不是静态展示卡片；挂载后会成为所选 Agent 的长期执行规范。知识检索、联网和 MCP 工具仍由 Agent 编排层按任务调用。</div>
    </div>
  );
}
