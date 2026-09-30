# GENERATED from argparse metadata
_ocpf_post_complete() {
  local cur prev
  COMPREPLY=()
  cur="${COMP_WORDS[COMP_CWORD]}"
  prev="${COMP_WORDS[COMP_CWORD-1]}"
  if (( COMP_CWORD == 1 )); then COMPREPLY=( $(compgen -W "accounts alerts campaign capabilities config console credentials doctor engagement evidence health help linkedin operations outcome-connectors performance portfolio publish receipt registry replenish run-due runtime schedule setup state threads vault work x" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "accounts" ]]; then COMPREPLY=( $(compgen -W "connect disable enable import list" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "alerts" ]]; then COMPREPLY=( $(compgen -W "configure connect disable enable send status" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "campaign" ]]; then COMPREPLY=( $(compgen -W "brief explain import receipts reconcile show" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "config" ]]; then COMPREPLY=( $(compgen -W "show" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "credentials" ]]; then COMPREPLY=( $(compgen -W "keyring" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "engagement" ]]; then COMPREPLY=( $(compgen -W "conversation dismiss draft process reconcile send status sync worker-status" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "evidence" ]]; then COMPREPLY=( $(compgen -W "check" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "linkedin" ]]; then COMPREPLY=( $(compgen -W "publish receipt refresh status" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "outcome-connectors" ]]; then COMPREPLY=( $(compgen -W "connect disable enable import status sync" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "performance" ]]; then COMPREPLY=( $(compgen -W "capture capture-due compare feedback outcomes review show" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "portfolio" ]]; then COMPREPLY=( $(compgen -W "calendar experiment plan policy reconcile refill replay snapshot status volume-audit watch" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "registry" ]]; then COMPREPLY=( $(compgen -W "import list resolve show" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "replenish" ]]; then COMPREPLY=( $(compgen -W "acknowledge-gap lock-status observe reconcile recover-generated refresh source status wait" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "runtime" ]]; then COMPREPLY=( $(compgen -W "install reconcile rollback status uninstall upgrade" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "schedule" ]]; then COMPREPLY=( $(compgen -W "cancel create inspect list" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "setup" ]]; then COMPREPLY=( $(compgen -W "activate admission adoption-review beta browser connect-x deactivate export inspect-bundle interactive onboard-x recovery-point restore resume start status verify verify-fresh" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "state" ]]; then COMPREPLY=( $(compgen -W "archive inventory migrate recover-segment segment verify" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "threads" ]]; then COMPREPLY=( $(compgen -W "publish receipt refresh status" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "vault" ]]; then COMPREPLY=( $(compgen -W "auth coverage credentials extend register status sync" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "work" ]]; then COMPREPLY=( $(compgen -W "status" -- "$cur") ); return; fi
  if (( COMP_CWORD == 2 )) && [[ "${COMP_WORDS[1]}" == "x" ]]; then COMPREPLY=( $(compgen -W "auth logout refresh status" -- "$cur") ); return; fi
  if (( COMP_CWORD == 3 )) && [[ "${COMP_WORDS[1]}" == "credentials" ]] && [[ "${COMP_WORDS[2]}" == "keyring" ]]; then COMPREPLY=( $(compgen -W "migrate restore status" -- "$cur") ); return; fi
  if (( COMP_CWORD == 3 )) && [[ "${COMP_WORDS[1]}" == "portfolio" ]] && [[ "${COMP_WORDS[2]}" == "experiment" ]]; then COMPREPLY=( $(compgen -W "reconcile report status stop" -- "$cur") ); return; fi
  if (( COMP_CWORD == 3 )) && [[ "${COMP_WORDS[1]}" == "replenish" ]] && [[ "${COMP_WORDS[2]}" == "source" ]]; then COMPREPLY=( $(compgen -W "disable enable evidence import list preview receipts" -- "$cur") ); return; fi
  if [[ "${COMP_WORDS[1]}" == "credentials" ]] && [[ "${COMP_WORDS[2]}" == "keyring" ]] && [[ "${COMP_WORDS[3]}" == "migrate" ]]; then
    case "$prev" in --provider) COMPREPLY=( $(compgen -W "x threads linkedin" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --provider" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "credentials" ]] && [[ "${COMP_WORDS[2]}" == "keyring" ]] && [[ "${COMP_WORDS[3]}" == "restore" ]]; then
    case "$prev" in --provider) COMPREPLY=( $(compgen -W "x threads linkedin" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --provider" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "credentials" ]] && [[ "${COMP_WORDS[2]}" == "keyring" ]] && [[ "${COMP_WORDS[3]}" == "status" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "portfolio" ]] && [[ "${COMP_WORDS[2]}" == "experiment" ]] && [[ "${COMP_WORDS[3]}" == "reconcile" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "portfolio" ]] && [[ "${COMP_WORDS[2]}" == "experiment" ]] && [[ "${COMP_WORDS[3]}" == "report" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--save" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "portfolio" ]] && [[ "${COMP_WORDS[2]}" == "experiment" ]] && [[ "${COMP_WORDS[3]}" == "status" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "portfolio" ]] && [[ "${COMP_WORDS[2]}" == "experiment" ]] && [[ "${COMP_WORDS[3]}" == "stop" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "replenish" ]] && [[ "${COMP_WORDS[2]}" == "source" ]] && [[ "${COMP_WORDS[3]}" == "disable" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--project" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "replenish" ]] && [[ "${COMP_WORDS[2]}" == "source" ]] && [[ "${COMP_WORDS[3]}" == "enable" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--expected-sha256 --project" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "replenish" ]] && [[ "${COMP_WORDS[2]}" == "source" ]] && [[ "${COMP_WORDS[3]}" == "evidence" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--project --sha" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "replenish" ]] && [[ "${COMP_WORDS[2]}" == "source" ]] && [[ "${COMP_WORDS[3]}" == "import" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --file" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "replenish" ]] && [[ "${COMP_WORDS[2]}" == "source" ]] && [[ "${COMP_WORDS[3]}" == "list" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "replenish" ]] && [[ "${COMP_WORDS[2]}" == "source" ]] && [[ "${COMP_WORDS[3]}" == "preview" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--project" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "replenish" ]] && [[ "${COMP_WORDS[2]}" == "source" ]] && [[ "${COMP_WORDS[3]}" == "receipts" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--project" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "accounts" ]] && [[ "${COMP_WORDS[2]}" == "connect" ]]; then
    case "$prev" in --provider) COMPREPLY=( $(compgen -W "x threads linkedin" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--account-id --client-id --credential-file --oauth --provider --reuse-default" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "accounts" ]] && [[ "${COMP_WORDS[2]}" == "disable" ]]; then
    case "$prev" in --provider) COMPREPLY=( $(compgen -W "x threads linkedin" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--account-id --provider" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "accounts" ]] && [[ "${COMP_WORDS[2]}" == "enable" ]]; then
    case "$prev" in --provider) COMPREPLY=( $(compgen -W "x threads linkedin" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--account-id --apply --expected-sha256 --provider" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "accounts" ]] && [[ "${COMP_WORDS[2]}" == "import" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --file" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "accounts" ]] && [[ "${COMP_WORDS[2]}" == "list" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "alerts" ]] && [[ "${COMP_WORDS[2]}" == "configure" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --file" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "alerts" ]] && [[ "${COMP_WORDS[2]}" == "connect" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--credential-file" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "alerts" ]] && [[ "${COMP_WORDS[2]}" == "disable" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "alerts" ]] && [[ "${COMP_WORDS[2]}" == "enable" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "alerts" ]] && [[ "${COMP_WORDS[2]}" == "send" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "alerts" ]] && [[ "${COMP_WORDS[2]}" == "status" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "campaign" ]] && [[ "${COMP_WORDS[2]}" == "brief" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--allocate --apply --expected-sha256 --file" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "campaign" ]] && [[ "${COMP_WORDS[2]}" == "explain" ]]; then
    case "$prev" in --provider) COMPREPLY=( $(compgen -W "x threads linkedin" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--campaign --json --provider" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "campaign" ]] && [[ "${COMP_WORDS[2]}" == "import" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--allocate --apply --expected-sha256 --file" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "campaign" ]] && [[ "${COMP_WORDS[2]}" == "receipts" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--brief-id --project" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "campaign" ]] && [[ "${COMP_WORDS[2]}" == "reconcile" ]]; then
    case "$prev" in --provider) COMPREPLY=( $(compgen -W "x threads linkedin" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --campaign --provider --schedule-id" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "campaign" ]] && [[ "${COMP_WORDS[2]}" == "show" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--campaign --provider" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "config" ]] && [[ "${COMP_WORDS[2]}" == "show" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "engagement" ]] && [[ "${COMP_WORDS[2]}" == "conversation" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--id" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "engagement" ]] && [[ "${COMP_WORDS[2]}" == "dismiss" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--id --reason" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "engagement" ]] && [[ "${COMP_WORDS[2]}" == "draft" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--id --text-file" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "engagement" ]] && [[ "${COMP_WORDS[2]}" == "process" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "engagement" ]] && [[ "${COMP_WORDS[2]}" == "reconcile" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --id" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "engagement" ]] && [[ "${COMP_WORDS[2]}" == "send" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--expected-sha256 --id --live" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "engagement" ]] && [[ "${COMP_WORDS[2]}" == "status" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--items" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "engagement" ]] && [[ "${COMP_WORDS[2]}" == "sync" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "engagement" ]] && [[ "${COMP_WORDS[2]}" == "worker-status" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "evidence" ]] && [[ "${COMP_WORDS[2]}" == "check" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --file --sha" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "linkedin" ]] && [[ "${COMP_WORDS[2]}" == "publish" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--allow-duplicate --campaign --file --live --stdin --text" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "linkedin" ]] && [[ "${COMP_WORDS[2]}" == "receipt" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--account-id --campaign" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "linkedin" ]] && [[ "${COMP_WORDS[2]}" == "refresh" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "linkedin" ]] && [[ "${COMP_WORDS[2]}" == "status" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "outcome-connectors" ]] && [[ "${COMP_WORDS[2]}" == "connect" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--credential-file --id" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "outcome-connectors" ]] && [[ "${COMP_WORDS[2]}" == "disable" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --id" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "outcome-connectors" ]] && [[ "${COMP_WORDS[2]}" == "enable" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --id" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "outcome-connectors" ]] && [[ "${COMP_WORDS[2]}" == "import" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --file" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "outcome-connectors" ]] && [[ "${COMP_WORDS[2]}" == "status" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "outcome-connectors" ]] && [[ "${COMP_WORDS[2]}" == "sync" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "performance" ]] && [[ "${COMP_WORDS[2]}" == "capture" ]]; then
    case "$prev" in --provider) COMPREPLY=( $(compgen -W "all x threads linkedin" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--campaign --provider" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "performance" ]] && [[ "${COMP_WORDS[2]}" == "capture-due" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--age-hours --apply --tolerance-hours" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "performance" ]] && [[ "${COMP_WORDS[2]}" == "compare" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "performance" ]] && [[ "${COMP_WORDS[2]}" == "feedback" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --disable --enable" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "performance" ]] && [[ "${COMP_WORDS[2]}" == "outcomes" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --file" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "performance" ]] && [[ "${COMP_WORDS[2]}" == "review" ]]; then
    case "$prev" in --provider) COMPREPLY=( $(compgen -W "x threads linkedin" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--account-id --age-hours --provider --tolerance-hours" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "performance" ]] && [[ "${COMP_WORDS[2]}" == "show" ]]; then
    case "$prev" in --provider) COMPREPLY=( $(compgen -W "x threads linkedin" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--campaign --provider" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "portfolio" ]] && [[ "${COMP_WORDS[2]}" == "calendar" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--horizon-minutes --json --save" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "portfolio" ]] && [[ "${COMP_WORDS[2]}" == "plan" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--horizon-minutes --json" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "portfolio" ]] && [[ "${COMP_WORDS[2]}" == "policy" ]]; then
    case "$prev" in --selection) COMPREPLY=( $(compgen -W "legacy fair" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--force --selection --write-default" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "portfolio" ]] && [[ "${COMP_WORDS[2]}" == "reconcile" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--json" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "portfolio" ]] && [[ "${COMP_WORDS[2]}" == "refill" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --horizon-minutes --json" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "portfolio" ]] && [[ "${COMP_WORDS[2]}" == "replay" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--file --output" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "portfolio" ]] && [[ "${COMP_WORDS[2]}" == "snapshot" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--horizon-minutes --output" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "portfolio" ]] && [[ "${COMP_WORDS[2]}" == "status" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--json" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "portfolio" ]] && [[ "${COMP_WORDS[2]}" == "volume-audit" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--date --json --timezone" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "portfolio" ]] && [[ "${COMP_WORDS[2]}" == "watch" ]]; then
    case "$prev" in --provider) COMPREPLY=( $(compgen -W "x threads linkedin" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--account-id --apply --issue --json --limit --project --provider" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "registry" ]] && [[ "${COMP_WORDS[2]}" == "import" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --file" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "registry" ]] && [[ "${COMP_WORDS[2]}" == "list" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "registry" ]] && [[ "${COMP_WORDS[2]}" == "resolve" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--account --project --provider" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "registry" ]] && [[ "${COMP_WORDS[2]}" == "show" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--project" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "replenish" ]] && [[ "${COMP_WORDS[2]}" == "acknowledge-gap" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --json --project" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "replenish" ]] && [[ "${COMP_WORDS[2]}" == "lock-status" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--json" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "replenish" ]] && [[ "${COMP_WORDS[2]}" == "observe" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --budget-seconds --json --project" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "replenish" ]] && [[ "${COMP_WORDS[2]}" == "reconcile" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--json" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "replenish" ]] && [[ "${COMP_WORDS[2]}" == "recover-generated" ]]; then
    case "$prev" in --provider) COMPREPLY=( $(compgen -W "x threads linkedin" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --json --provider --review-file" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "replenish" ]] && [[ "${COMP_WORDS[2]}" == "refresh" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --json --project" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "replenish" ]] && [[ "${COMP_WORDS[2]}" == "status" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--json" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "replenish" ]] && [[ "${COMP_WORDS[2]}" == "wait" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--json --poll-interval --timeout" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "runtime" ]] && [[ "${COMP_WORDS[2]}" == "install" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "runtime" ]] && [[ "${COMP_WORDS[2]}" == "reconcile" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "runtime" ]] && [[ "${COMP_WORDS[2]}" == "rollback" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "runtime" ]] && [[ "${COMP_WORDS[2]}" == "status" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "runtime" ]] && [[ "${COMP_WORDS[2]}" == "uninstall" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "runtime" ]] && [[ "${COMP_WORDS[2]}" == "upgrade" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --revision" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "schedule" ]] && [[ "${COMP_WORDS[2]}" == "cancel" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "schedule" ]] && [[ "${COMP_WORDS[2]}" == "create" ]]; then
    case "$prev" in --provider) COMPREPLY=( $(compgen -W "x threads linkedin" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--at --campaign --provider --timezone" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "schedule" ]] && [[ "${COMP_WORDS[2]}" == "inspect" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--json" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "schedule" ]] && [[ "${COMP_WORDS[2]}" == "list" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--all --json" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "activate" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --config-dir --expected-sha256 --json --recovery-resolution --runtime-root --session-id --state-dir --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "admission" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--config-dir --json --production --state-dir --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "adoption-review" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--beta-id --minimum-hours --minimum-observations --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "beta" ]]; then
    case "$prev" in --action) COMPREPLY=( $(compgen -W "preflight enroll observe status decide" -- "$cur") ); return ;; esac
    case "$prev" in --ring) COMPREPLY=( $(compgen -W "rehearsal owner-canary" -- "$cur") ); return ;; esac
    case "$prev" in --operator-outcome) COMPREPLY=( $(compgen -W "healthy issue rollback" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--action --beta-id --config-dir --minimum-hours --minimum-observations --operator-outcome --ring --runtime-root --state-dir --target-revision --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "browser" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--no-open --port --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "connect-x" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--client-id --client-secret-file --prompt-client-secret --redirect-uri --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "deactivate" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --json --reason --runtime-root --session-id --state-dir --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "export" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --json --output --session-id --source-config-dir --source-revision --source-state-dir --source-version --state-registry-version --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "inspect-bundle" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--bundle --expected-bundle-sha256 --json --recovery --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "interactive" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "onboard-x" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --campaign-id --json --label --project --session-id --source-id --stdin --text --text-file --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "recovery-point" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --json --output --session-id --source-config-dir --source-revision --source-state-dir --source-version --state-registry-version --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "restore" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--bundle --expected-bundle-sha256 --json --machine-label --max-data-loss-minutes --recovery --source-lost-at --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "resume" ]]; then
    case "$prev" in --pace) COMPREPLY=( $(compgen -W "occasional regular active high custom" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--daily-originals --json --pace --session-id --timezone --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "start" ]]; then
    case "$prev" in --mode) COMPREPLY=( $(compgen -W "fresh explore migrate recover different_operator" -- "$cur") ); return ;; esac
    case "$prev" in --pace) COMPREPLY=( $(compgen -W "occasional regular active high custom" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--daily-originals --json --machine-label --mode --operator-label --pace --timezone --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "status" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--json --session-id --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "verify" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--json --provider-readiness --recovery --session-id --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "setup" ]] && [[ "${COMP_WORDS[2]}" == "verify-fresh" ]]; then
    case "$prev" in --provider) COMPREPLY=( $(compgen -W "x threads linkedin" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--expected-account-id --json --provider --session-id --workspace" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "state" ]] && [[ "${COMP_WORDS[2]}" == "archive" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --output" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "state" ]] && [[ "${COMP_WORDS[2]}" == "inventory" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "state" ]] && [[ "${COMP_WORDS[2]}" == "migrate" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "state" ]] && [[ "${COMP_WORDS[2]}" == "recover-segment" ]]; then
    case "$prev" in --ledger) COMPREPLY=( $(compgen -W "receipts schedules performance" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --ledger" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "state" ]] && [[ "${COMP_WORDS[2]}" == "segment" ]]; then
    case "$prev" in --ledger) COMPREPLY=( $(compgen -W "receipts schedules performance" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --expected-sha256 --keep-lines --ledger" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "state" ]] && [[ "${COMP_WORDS[2]}" == "verify" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "threads" ]] && [[ "${COMP_WORDS[2]}" == "publish" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--allow-duplicate --campaign --file --live --stdin --text" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "threads" ]] && [[ "${COMP_WORDS[2]}" == "receipt" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--account-id --campaign" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "threads" ]] && [[ "${COMP_WORDS[2]}" == "refresh" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "threads" ]] && [[ "${COMP_WORDS[2]}" == "status" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "vault" ]] && [[ "${COMP_WORDS[2]}" == "auth" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--client-file --port" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "vault" ]] && [[ "${COMP_WORDS[2]}" == "coverage" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--inventory" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "vault" ]] && [[ "${COMP_WORDS[2]}" == "credentials" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--file" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "vault" ]] && [[ "${COMP_WORDS[2]}" == "extend" ]]; then
    case "$prev" in --provider) COMPREPLY=( $(compgen -W "x threads linkedin" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--account --apply --expected-sha256 --provider --vault-id" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "vault" ]] && [[ "${COMP_WORDS[2]}" == "register" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --enable --expected-sha256 --file" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "vault" ]] && [[ "${COMP_WORDS[2]}" == "status" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "vault" ]] && [[ "${COMP_WORDS[2]}" == "sync" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--apply --vault-id" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "work" ]] && [[ "${COMP_WORDS[2]}" == "status" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--json" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "x" ]] && [[ "${COMP_WORDS[2]}" == "auth" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "x" ]] && [[ "${COMP_WORDS[2]}" == "logout" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "x" ]] && [[ "${COMP_WORDS[2]}" == "refresh" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "x" ]] && [[ "${COMP_WORDS[2]}" == "status" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "capabilities" ]]; then
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "console" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--port --snapshot" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "doctor" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--deep --json" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "health" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--executing-minutes --hours --json --overdue-minutes --skip-timers --source-age-minutes" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "help" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--json" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "operations" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--all --project --save" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "publish" ]]; then
    case "$prev" in --provider) COMPREPLY=( $(compgen -W "x" -- "$cur") ); return ;; esac
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--allow-duplicate --campaign --client-id --client-secret --file --live --provider --redirect-uri --stdin --text" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "receipt" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--account-id --campaign --provider" -- "$cur") ); return; fi
    return
  fi
  if [[ "${COMP_WORDS[1]}" == "run-due" ]]; then
    if [[ "$cur" == -* ]]; then COMPREPLY=( $(compgen -W "--check --limit" -- "$cur") ); return; fi
    return
  fi
}
complete -F _ocpf_post_complete ocpf-post
