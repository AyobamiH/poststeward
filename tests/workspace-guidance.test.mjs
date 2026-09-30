import test from 'node:test';
import assert from 'node:assert/strict';
import { nextWorkspaceTask } from '../public/workspace-guidance.js';
test('next-step guidance treats disconnected accounts and unknown state conservatively', () => {
  assert.equal(nextWorkspaceTask({accounts:[{active:false}]}).href, '#destinations');
  assert.equal(nextWorkspaceTask({accounts:[{active:true}],projects:[]}).href, '#project');
  assert.equal(nextWorkspaceTask({accounts:[{active:true}],projects:[{}],receipts:[{status:'pending_approval'}]}).href, '#evidence-panel');
  const unknown = nextWorkspaceTask({accounts:[{active:true}],projects:[{}],receipts:[{status:'future_unknown'}]});
  assert.equal(unknown.href, '#publishing');
  assert.doesNotMatch(unknown.title, /published|verified|successful/i);
});
test('paused guidance does not suggest automatic resumption or a new external effect', () => {
  const task = nextWorkspaceTask({paused:true,accounts:[],receipts:[{status:'ambiguous_effect'}]});
  assert.equal(task.href, '#evidence-panel');
  assert.match(task.copy, /Review why/);
  assert.doesNotMatch(task.action, /Resume|Publish/i);
});
