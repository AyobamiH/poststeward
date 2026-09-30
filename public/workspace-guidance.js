/* Fixed guidance from loaded server state. Never infers publication or takes authority. */
export function nextWorkspaceTask({accounts = [], projects = [], receipts = [], paused = false} = {}) {
  if (paused) return {title:'Publishing is paused',copy:'Existing records remain available. Review why publishing was paused before using Resume publishing.',href:'#evidence-panel',action:'Review schedules and results'};
  if (!accounts.some(account => account.active === true)) return {title:'Connect your first social account',copy:'Start with X or Threads. LinkedIn Page connections require provider approval.',href:'#destinations',action:'Connect an account'};
  if (!projects.length) return {title:'Create a publishing project',copy:'Group the connected accounts this project may use, then add the exact text you want to publish.',href:'#project',action:'Create a project'};
  if (receipts.some(receipt => receipt.status === 'pending_approval')) return {title:'A delivery needs your approval',copy:'Review the exact copy, account and time. Agent access alone does not authorise publication.',href:'#evidence-panel',action:'Review delivery receipts'};
  return {title:'Your publishing workspace',copy:'Create reviewed copy, schedule a delivery, or check what happened. A scheduled receipt confirms a reservation; Verified confirms provider readback.',href:'#publishing',action:'Review and schedule a post'};
}
export function renderWorkspaceTask(snapshot) {
  const root = document.getElementById('workspace-next-step');
  if (!root) return;
  const task = nextWorkspaceTask(snapshot);
  root.querySelector('h2').textContent = task.title;
  root.querySelector('p').textContent = task.copy;
  const link = root.querySelector('a'); link.href = task.href; link.textContent = task.action;
}
