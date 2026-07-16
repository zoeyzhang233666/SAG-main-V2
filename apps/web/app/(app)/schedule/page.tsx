"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { CalendarDays, Clock3, MessageCircle, Play, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { api, ApiError } from "@/lib/api";
import type { Agent, ScheduleFrequency, ScheduleTask } from "@/lib/types";
import { useApp } from "@/components/features/app-shell";
import { PageHeader } from "@/components/features/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

const FREQUENCY_LABELS: Record<ScheduleFrequency, string> = {
  once: "执行一次",
  daily: "每天",
  weekdays: "工作日",
  weekly: "每周",
};

function defaultDateTime() {
  const value = new Date(Date.now() + 60 * 60 * 1000);
  value.setSeconds(0, 0);
  return new Date(value.getTime() - value.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

export default function SchedulePage() {
  const router = useRouter();
  const { timezone, replaceAgent } = useApp();
  const [tasks, setTasks] = React.useState<ScheduleTask[] | null>(null);
  const [agents, setAgents] = React.useState<Agent[]>([]);
  const [open, setOpen] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [running, setRunning] = React.useState("");
  const [draft, setDraft] = React.useState({
    name: "",
    prompt: "",
    agentId: "",
    frequency: "once" as ScheduleFrequency,
    nextRunAt: defaultDateTime(),
  });

  const load = React.useCallback(async () => {
    try {
      const [taskRows, agentRows] = await Promise.all([api.listSchedules(), api.listAgents()]);
      setTasks(taskRows);
      setAgents(agentRows);
      setDraft((value) => ({ ...value, agentId: value.agentId || agentRows[0]?.id || "" }));
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "日程加载失败");
    }
  }, []);
  React.useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 5_000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function createTask() {
    if (!draft.name.trim() || !draft.prompt.trim() || !draft.agentId || !draft.nextRunAt) return;
    setSaving(true);
    try {
      await api.createSchedule({
        agent_id: draft.agentId,
        name: draft.name.trim(),
        prompt: draft.prompt.trim(),
        frequency: draft.frequency,
        timezone,
        next_run_at: new Date(draft.nextRunAt).toISOString(),
        enabled: true,
      });
      setOpen(false);
      setDraft((value) => ({ ...value, name: "", prompt: "", nextRunAt: defaultDateTime() }));
      await load();
      toast.success("日程任务已创建");
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "创建失败");
    } finally {
      setSaving(false);
    }
  }

  async function runNow(task: ScheduleTask) {
    setRunning(task.id);
    try {
      await api.runSchedule(task.id);
      await load();
      toast.success("任务已交给龙虾执行");
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "执行失败");
    } finally {
      setRunning("");
    }
  }

  async function toggle(task: ScheduleTask, enabled: boolean) {
    try {
      const updated = await api.updateSchedule(task.id, { enabled });
      setTasks((items) => items?.map((item) => item.id === updated.id ? updated : item) ?? []);
    } catch (error) {
      toast.error(error instanceof ApiError ? error.message : "更新失败");
    }
  }

  async function remove(task: ScheduleTask) {
    if (!window.confirm(`确认删除“${task.name}”吗？`)) return;
    await api.deleteSchedule(task.id);
    setTasks((items) => items?.filter((item) => item.id !== task.id) ?? []);
  }

  async function openResult(task: ScheduleTask) {
    if (!task.last_thread_id) return;
    const agent = agents.find((item) => item.id === task.agent_id);
    if (agent) replaceAgent(agent);
    router.push(`/chat/${task.last_thread_id}`);
  }

  return (
    <div className="mx-auto w-full max-w-6xl space-y-5 p-4 md:p-6">
      <PageHeader title="我的日程" description="让龙虾按时执行研究、预警和资料整理；运行结果会保存为可继续追问的完整会话。" actions={<Button onClick={() => setOpen(true)}><Plus className="size-4" />新建任务</Button>} />
      <div className="grid gap-3 md:grid-cols-3">
        <div className="rounded-xl border bg-card p-4 shadow-soft"><div className="text-xs text-muted-foreground">全部任务</div><div className="mt-1 text-2xl font-semibold">{tasks?.length ?? 0}</div></div>
        <div className="rounded-xl border bg-card p-4 shadow-soft"><div className="text-xs text-muted-foreground">已启用</div><div className="mt-1 text-2xl font-semibold">{tasks?.filter((task) => task.enabled).length ?? 0}</div></div>
        <div className="rounded-xl border bg-card p-4 shadow-soft"><div className="text-xs text-muted-foreground">正在执行</div><div className="mt-1 text-2xl font-semibold">{tasks?.filter((task) => task.last_status === "running").length ?? 0}</div></div>
      </div>
      <div className="space-y-3">
        {tasks === null ? Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-32 rounded-xl" />) : tasks.length === 0 ? (
          <div className="grid min-h-72 place-items-center rounded-xl border border-dashed bg-card/40 p-6 text-center"><div><CalendarDays className="mx-auto size-9 text-muted-foreground" /><h2 className="mt-3 text-sm font-medium">还没有日程任务</h2><p className="mt-1 text-xs text-muted-foreground">创建一次性或周期任务，到点后由指定龙虾自动执行。</p></div></div>
        ) : tasks.map((task) => {
          const agent = agents.find((item) => item.id === task.agent_id);
          return (
            <article key={task.id} className="rounded-xl border bg-card p-4 shadow-soft">
              <div className="flex flex-col gap-4 md:flex-row md:items-start">
                <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-primary/10 text-lg">{agent?.avatar || "🦞"}</div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2"><h2 className="text-sm font-semibold">{task.name}</h2><Badge variant="secondary">{FREQUENCY_LABELS[task.frequency]}</Badge><Badge variant={task.last_status === "failed" ? "destructive" : task.last_status === "succeeded" ? "default" : "outline"}>{task.last_status === "running" ? "执行中" : task.last_status === "succeeded" ? "已完成" : task.last_status === "failed" ? "执行失败" : "等待执行"}</Badge></div>
                  <p className="mt-1.5 line-clamp-2 text-xs leading-5 text-muted-foreground">{task.prompt}</p>
                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground"><span>{agent?.name || "龙虾"}</span>{task.next_run_at && <span className="inline-flex items-center gap-1"><Clock3 className="size-3" />下次 {new Date(task.next_run_at).toLocaleString()}</span>}{task.last_error && <span className="text-destructive">{task.last_error}</span>}</div>
                </div>
                <div className="flex shrink-0 items-center gap-2"><Switch checked={task.enabled} onCheckedChange={(checked) => void toggle(task, checked)} aria-label="启用任务" /><Button variant="outline" size="sm" disabled={task.last_status === "running" || running === task.id} onClick={() => void runNow(task)}><Play className="size-3.5" />立即执行</Button>{task.last_thread_id && <Button variant="outline" size="icon" aria-label="查看结果" onClick={() => void openResult(task)}><MessageCircle className="size-4" /></Button>}<Button variant="ghost" size="icon" aria-label="删除任务" onClick={() => void remove(task)}><Trash2 className="size-4" /></Button></div>
              </div>
            </article>
          );
        })}
      </div>
      <Dialog open={open} onOpenChange={setOpen}><DialogContent className="max-w-xl"><DialogHeader><DialogTitle>新建日程任务</DialogTitle><DialogDescription>任务会持久保存，到点后由后台调用指定龙虾并保存完整结果。</DialogDescription></DialogHeader><Input value={draft.name} onChange={(event) => setDraft((value) => ({ ...value, name: event.target.value }))} placeholder="任务名称，例如：华东丙烯早报" /><Textarea rows={7} value={draft.prompt} onChange={(event) => setDraft((value) => ({ ...value, prompt: event.target.value }))} placeholder="这是发给龙虾的具体执行指令，尽量写清品种、地区、时间范围和输出格式。" /><div className="grid gap-3 sm:grid-cols-2"><Select value={draft.agentId} onValueChange={(agentId) => setDraft((value) => ({ ...value, agentId }))}><SelectTrigger><SelectValue placeholder="选择龙虾" /></SelectTrigger><SelectContent>{agents.map((agent) => <SelectItem key={agent.id} value={agent.id}>{agent.avatar} {agent.name}</SelectItem>)}</SelectContent></Select><Select value={draft.frequency} onValueChange={(frequency) => setDraft((value) => ({ ...value, frequency: frequency as ScheduleFrequency }))}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{Object.entries(FREQUENCY_LABELS).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}</SelectContent></Select></div><Input type="datetime-local" value={draft.nextRunAt} onChange={(event) => setDraft((value) => ({ ...value, nextRunAt: event.target.value }))} /><Button disabled={saving || !draft.name.trim() || !draft.prompt.trim() || !draft.agentId} onClick={() => void createTask()}>{saving ? "正在创建…" : "创建并加入日程"}</Button></DialogContent></Dialog>
    </div>
  );
}
