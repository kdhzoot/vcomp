import datetime,json,os,subprocess,sys,time,shutil
from pathlib import Path
launch=Path(__file__).resolve().parent
plan=json.loads((launch/'preflight.json').read_text())
root=Path(plan['artifact_root'])
state={'launcher_pid':os.getpid(),'started':datetime.datetime.now(datetime.timezone.utc).isoformat(),'argv':plan['argv'],'state':'starting'}
def save():
 (launch/'launcher_status.json').write_text(json.dumps(state,indent=2)+'\n')
save()
with (launch/'controller.log').open('xb') as log:
 child=subprocess.Popen(plan['argv'],cwd=plan['cwd'],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 state.update(runner_pid=child.pid,state='running');save()
 while child.poll() is None and not root.exists():time.sleep(.1)
 if root.exists():
  dest=root/'seqfix_source_snapshot';shutil.copytree(launch/'source_snapshot',dest)
  (root/'seqfix_source_hashes.json').write_text(json.dumps({'captured_before_launch':plan['created'],'sources':plan['source_sha256'],'snapshot':'seqfix_source_snapshot'},indent=2)+'\n')
  (root/'launch_provenance.json').write_text(json.dumps({'launch_directory':str(launch),'runner_pid':child.pid,'argv':plan['argv'],'preflight':plan},indent=2)+'\n')
 code=child.wait()
 state.update(state='finished',exit_code=code,finished=datetime.datetime.now(datetime.timezone.utc).isoformat());save()
 sys.exit(code)
