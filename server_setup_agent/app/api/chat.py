from fastapi import APIRouter, HTTPException
from loguru import logger
from typing import Optional, Union
import os

from app.agents.supervisor import supervisor
from app.api.schemas import QueryRequest, QueryResponse, EncryptedRequest
from app.services.sanitizer_service import register_credentials, clear_credentials
from app.services.conversation_service import NeedsInputError, conversation_store
from app.utils.encryption import decrypt_payload

router = APIRouter()


def decrypt_query_if_needed(req: Union[QueryRequest, EncryptedRequest]) -> QueryRequest:
    """
    Decrypt the request if it's encrypted, otherwise return as-is.
    
    Args:
        req: Either plain QueryRequest or EncryptedRequest
        
    Returns:
        Decrypted QueryRequest
    """
    # If it's already a QueryRequest, return it
    if isinstance(req, QueryRequest):
        return req
    
    # If it has encrypted_data, decrypt it
    if hasattr(req, 'encrypted_data'):
        encryption_key = os.getenv('ENCRYPTION_KEY', 'my-super-secret-encryption-key')
        
        try:
            decrypted_data = decrypt_payload(req.encrypted_data, encryption_key)
            logger.info("[API] ✓ Decrypted query payload")
            return QueryRequest(**decrypted_data)
        except Exception as e:
            logger.error(f"[API] ✗ Decryption failed: {str(e)}")
            raise HTTPException(status_code=400, detail=f"Decryption failed: {str(e)}")
    
    raise HTTPException(status_code=400, detail="Invalid request format")


def _dispatch(agent_name: str, query: str, request: QueryRequest) -> str:
    creds           = request.credentials
    executor_type   = creds.executor_type if creds else "local"
    executor_config = creds.to_config() if creds else {}
    dp              = request.deployment_params
    sp              = request.setup_params
    label           = executor_config.get("host", "server")

    if agent_name == "setup":
        from app.agents.setup_agent import SetupAgent
        agent = SetupAgent(executor_type=executor_type, executor_config=executor_config, server_label=label)
        # Tell the agent which user it's connected as so it knows whether
        # to run bootstrap_user (only valid when connected as root)
        agent._connected_as = executor_config.get("username", "root")
        if sp:
            agent._prefill = {
                "new_username":    sp.new_username,
                "new_password":    sp.new_password,
                "your_public_key": sp.your_public_key,
            }
            agent._api_mode = True
        else:
            agent._api_mode = True  # always API mode when called via HTTP
        return agent.execute_task(query)

    if agent_name == "deployment":
        from app.agents.deployment_agent import DeploymentAgent
        agent = DeploymentAgent(executor_type=executor_type, executor_config=executor_config)
        if dp:
            agent._prefill = {
                "branch":          dp.branch,
                "process_manager": dp.process_manager,
                "env_vars":        dp.env_vars,
            }
        # Token from server credentials takes priority; fall back to app config
        from app.core.config import settings
        agent._github_token = (creds.github_token if creds and creds.github_token
                               else settings.GITHUB_TOKEN)
        return agent.execute_task(query)

    if agent_name == "user_management":
        from app.agents.user_management_agent import UserManagementAgent
        agent = UserManagementAgent(executor_type=executor_type, executor_config=executor_config, server_label=label)
        return agent.execute_task(query)

    if agent_name in ("monitoring", "monitoring_health"):
        from app.agents.monitoring_agent import MonitoringAgent
        agent = MonitoringAgent(executor_type=executor_type, executor_config=executor_config, server_label=label)
        return agent.check_all()

    if agent_name == "monitoring_info":
        from app.agents.monitoring_agent import MonitoringAgent
        agent = MonitoringAgent(executor_type=executor_type, executor_config=executor_config, server_label=label)
        return agent.server_info()

    if agent_name == "security":
        from app.agents.security_agent import SecurityAgent
        agent = SecurityAgent(executor_type=executor_type, executor_config=executor_config, server_label=label)
        return agent.full_audit()

    if agent_name == "maintenance":
        from app.agents.maintenance_agent import MaintenanceAgent
        agent = MaintenanceAgent(executor_type=executor_type, executor_config=executor_config, server_label=label)
        return agent.execute_task(query)

    if agent_name == "troubleshooting":
        from app.agents.troubleshooting_agent import TroubleshootingAgent
        agent = TroubleshootingAgent(executor_type=executor_type, executor_config=executor_config, server_label=label)
        return agent.execute_task(query)

    if agent_name == "ops":
        from app.agents.ops_agent import OpsAgent
        agent = OpsAgent(executor_type=executor_type, executor_config=executor_config, server_label=label)
        return agent.execute_task(query)

    if agent_name == "general":
        from app.agents.general_agent import GeneralAgent
        agent = GeneralAgent()
        return agent.execute_task(query)

    return f"The '{agent_name}' agent is not yet fully implemented."


# ── Deterministic pre-router — intercepts read-only queries before the LLM supervisor ──
# These patterns are unambiguous enough that we don't need an LLM to classify them.
_INFO_PATTERNS = (
    "show me the server",
    "show server",
    "server configuration",
    "server config",
    "what's installed",
    "what is installed",
    "whats installed",
    "installed packages",
    "installed software",
    "list packages",
    "list installed",
    "what do we have",
    "what packages",
    "all packages",
    "software versions",
    "package versions",
    "show configuration",
    "show packages",
    "what's on the server",
    "what is on the server",
    "server info",
    "server status",
    "server stack",
    "show stack",
    "what stack",
)

_HEALTH_PATTERNS = (
    "health check",
    "server health",
    "is everything running",
    "check services",
    "check all",
    "are services up",
    "service status",
)


def _pre_route(query: str) -> Optional[str]:
    """
    Returns an agent name if the query matches a deterministic pattern,
    otherwise None (fall through to LLM supervisor).
    """
    q = query.lower().strip()
    
    # Redeploy detection: route to ops agent for simple pull + restart
    # Matches: "redeploy", "re-deploy", "update deploy", etc.
    redeploy_keywords = ("redeploy", "re-deploy", "re deploy", "update deployment", "upgrade deployment", "pull latest", "update app")
    if any(keyword in q for keyword in redeploy_keywords):
        return "ops"
    
    if any(p in q for p in _INFO_PATTERNS):
        return "monitoring_info"
    if any(p in q for p in _HEALTH_PATTERNS):
        return "monitoring_health"
    return None


@router.post("/query", response_model=QueryResponse)
async def handle_query(request: Union[QueryRequest, EncryptedRequest]):
    # Decrypt if needed
    request = decrypt_query_if_needed(request)
    
    creds = request.credentials
    if creds:
        register_credentials(
            host=creds.host,
            username=creds.username,
            password=creds.password,
            key_filename=creds.key_filename,
        )

    agent_name = "setup"
    try:
        # ── Resume a pending conversation ──────────────────────────────────
        if request.conversation_id:
            state = conversation_store.get(request.conversation_id)
            if state:
                return await _resume_conversation(state, request.query, request)
            else:
                # Expired or invalid — treat as new query
                logger.warning(f"conversation_id {request.conversation_id} not found or expired")

        # ── New query ──────────────────────────────────────────────────────
        # Deterministic pre-route — bypasses LLM supervisor for unambiguous patterns
        pre = _pre_route(request.query)
        if pre:
            agent_name = pre
            reason     = "Matched deterministic pattern"
            logger.info(f"Pre-router matched '{agent_name}': {request.query!r}")
        else:
            decision   = supervisor.route(request.query)
            agent_name = decision.get("agent", "general")
            reason     = decision.get("reason", "")
            logger.info(f"Supervisor routed to '{agent_name}': {reason}")

        result = _dispatch(agent_name, request.query, request)
        return QueryResponse(agent=agent_name, reason=reason, result=result)

    except NeedsInputError as nie:
        cid = conversation_store.save({
            "agent_name":  agent_name,
            "request":     request.model_dump(),  # Store complete request with credentials
            "question":    nie.question,
            "agent_state": nie.state,
        })
        return QueryResponse(
            agent=agent_name,
            reason="Needs more information from user",
            result=None,
            needs_input=True,
            question=nie.question,
            conversation_id=cid,
        )

    except Exception as exc:
        logger.exception("Unhandled error in handle_query")
        raise HTTPException(status_code=500, detail=str(exc))

    finally:
        clear_credentials()


async def _resume_conversation(state: dict, user_answer: str, request: QueryRequest) -> QueryResponse:
    """
    Resume a paused conversation with the user's answer.
    Rebuilds the original request with the answer injected into the right field.
    """
    from app.api.schemas import SetupParams, DeploymentParams

    agent_name   = state.get("agent_name", "setup")
    agent_state  = state.get("agent_state", {})
    step         = agent_state.get("step", "")
    orig_request = state.get("request", {})
    prefill      = agent_state.get("prefill", {}) or {}

    # Inject user's answer into the right prefill field based on step
    if step == "gather":
        # User answered the clarification question — combine original query + answer
        # so the LLM has all info in a single message and won't ask again.
        original_query = agent_state.get("query", orig_request.get("query", ""))
        answer = user_answer.strip()
        if answer.lower() in ("yes", "y", "ok", "proceed", "sure"):
            new_query = original_query
        else:
            new_query = f"{original_query}. {answer}"
        prefill["_llm_answer"] = user_answer

    elif step == "suggestions":
        answer = user_answer.strip().lower()
        if answer in ("yes", "y"):
            prefill["accept_suggestions"] = "yes"
        elif answer in ("no", "n", "skip", ""):
            prefill["accept_suggestions"] = "no"
            # Skip suggestions — go straight to confirm with original plan
            prefill["skip_suggestions"] = True
        else:
            prefill["accept_suggestions"] = answer  # partial e.g. "1,3"
        new_query = orig_request.get("query", "")

    elif step == "confirm":
        # Strip quotes and clean up the answer
        answer = user_answer.strip().lower().strip("'\"")
        negative = {"no", "n", "cancel", "stop", "abort", "nope"}
        proceed_words = {"proceed", "proceed to start", "start", "yes", "y", "ok", "go", "go ahead", "confirm", "sure", "done", "yep", "yeah"}
        if answer in negative:
            return QueryResponse(agent=agent_name, reason="Cancelled", result="Setup cancelled.")
        elif answer in proceed_words or answer.startswith("proceed"):
            # User confirmed — carry the already-built plan forward so _gather_context skips the LLM
            ctx_partial = agent_state.get("ctx_partial", {})
            prefill.update(ctx_partial)
            prefill["confirmed"] = True
        else:
            # User wants to add something — replay the LLM with the addition injected
            # Restore the LLM messages from the saved confirm state and append the user's request
            ctx_partial = agent_state.get("ctx_partial", {})
            saved_messages = agent_state.get("messages", [])
            prefill["_extra_request"] = user_answer
            prefill["_resume_messages"] = saved_messages
            # Do NOT set confirmed — let _gather_context re-run LLM with the extra request
        new_query = orig_request.get("query", "")

    elif step == "need_username":
        prefill["new_username"] = user_answer.strip()
        new_query = orig_request.get("query", "")

    elif step == "need_password":
        prefill["new_password"] = user_answer.strip()
        new_query = orig_request.get("query", "")

    elif step == "need_pubkey":
        prefill["your_public_key"] = user_answer.strip()
        new_query = orig_request.get("query", "")

    elif step == "need_github_token":
        prefill["github_token"] = user_answer.strip()
        # Merge ctx_partial so early-exit path has all core fields
        prefill.update({k: v for k, v in agent_state.get("ctx_partial", {}).items() if k not in prefill or not prefill[k]})
        new_query = orig_request.get("query", "")

    elif step == "need_app_type":
        answer = user_answer.strip().lower()
        suggested = agent_state.get("suggested", "backend")
        if answer in ("frontend", "front", "f", "ui"):
            prefill["app_type"] = "frontend"
        elif answer in ("backend", "back", "b", "api"):
            prefill["app_type"] = "backend"
        elif answer in ("yes", "y", ""):
            # User confirmed the suggestion
            prefill["app_type"] = suggested
        else:
            prefill["app_type"] = suggested  # fallback to suggestion
        # Merge ctx_partial so all core fields survive the resume
        prefill.update({k: v for k, v in agent_state.get("ctx_partial", {}).items() if k not in prefill or not prefill[k]})
        new_query = orig_request.get("query", "")

    elif step == "need_deploy_mode":
        answer = user_answer.strip().lower()
        default_path = agent_state.get("default_path", "")
        app_name = agent_state.get("app_name", "")
        
        if answer == "1" or answer == "direct" or not answer:
            # User chose direct path (option 1)
            prefill["clone_dir"] = default_path
        elif answer == "2" or answer == "subdirectory" or answer == "sub":
            # User chose subdirectory path (option 2)
            prefill["clone_dir"] = f"{default_path}/{app_name}".rstrip("/")
        elif answer.startswith("/"):
            # User provided custom path - check if it ends with app name
            custom_path = answer.rstrip("/")
            # If path doesn't end with app_name, append it (for multi-app scenarios)
            if not custom_path.endswith(app_name):
                prefill["clone_dir"] = f"{custom_path}/{app_name}".rstrip("/")
            else:
                prefill["clone_dir"] = custom_path
        else:
            # Treat as custom subdirectory name
            prefill["clone_dir"] = f"{default_path}/{answer}".rstrip("/")
        
        # Merge ctx_partial so all core fields survive the resume
        prefill.update({k: v for k, v in agent_state.get("ctx_partial", {}).items() if k not in prefill or not prefill[k]})
        new_query = orig_request.get("query", "")

    elif step == "need_deploy_path":
        answer = user_answer.strip()
        default_path = agent_state.get("default_path", "")
        if not answer or answer.lower() in ("default", "skip", ""):
            # User accepted default path
            prefill["clone_dir"] = default_path
        else:
            # User provided custom path
            prefill["clone_dir"] = answer
        # Merge ctx_partial so all core fields survive the resume
        prefill.update({k: v for k, v in agent_state.get("ctx_partial", {}).items() if k not in prefill or not prefill[k]})
        new_query = orig_request.get("query", "")

    elif step == "need_process_manager":
        default_pm = agent_state.get("default_pm", "pm2")
        pm = user_answer.strip().lower()
        prefill["process_manager"] = pm if pm in ("pm2", "systemd", "docker") else default_pm
        # Merge ctx_partial so early-exit path has all core fields
        prefill.update({k: v for k, v in agent_state.get("ctx_partial", {}).items() if k not in prefill or not prefill[k]})
        new_query = orig_request.get("query", "")

    elif step == "need_branch":
        branches = agent_state.get("branches", [])
        answer   = user_answer.strip()
        if answer.isdigit():
            idx = int(answer) - 1
            prefill["branch"] = branches[idx] if 0 <= idx < len(branches) else (branches[0] if branches else "main")
        else:
            prefill["branch"] = answer or (branches[0] if branches else "main")
        # Merge ctx_partial so early-exit path has all core fields
        prefill.update({k: v for k, v in agent_state.get("ctx_partial", {}).items() if k not in prefill or not prefill[k]})
        new_query = orig_request.get("query", "")

    elif step == "need_port_conflict":
        answer = user_answer.strip().lower()
        if answer.isdigit():
            prefill["port_conflict_answer"] = answer
        else:
            prefill["port_conflict_answer"] = "continue"
        prefill.update({k: v for k, v in agent_state.get("ctx_partial", {}).items() if k not in prefill or not prefill[k]})
        new_query = orig_request.get("query", "")

    elif step == "need_sudo_password_user_mgmt":
        # User provided sudo password for user management
        sudo_password = user_answer.strip()
        if not sudo_password:
            return QueryResponse(agent=agent_name, reason="Cancelled", result="User creation cancelled - no password provided.")
        
        username = agent_state.get("username", "")
        public_key = agent_state.get("public_key", "")
        
        # Get credentials from request
        creds = request.credentials
        if not creds and "request" in state:
            orig_creds_dict = state.get("request", {}).get("credentials")
            if orig_creds_dict:
                from app.api.schemas import ServerCredentials
                creds = ServerCredentials(**orig_creds_dict)
        
        # Add sudo_password to credentials
        if creds:
            creds.sudo_password = sudo_password
        
        executor_type = creds.executor_type if creds else "local"
        executor_config = creds.to_config() if creds else {}
        label = executor_config.get("host", "server")
        
        # Recreate agent with sudo password
        from app.agents.user_management_agent import UserManagementAgent
        agent = UserManagementAgent(executor_type=executor_type, executor_config=executor_config, server_label=label)
        result = agent.add_user(username, public_key)
        return QueryResponse(agent=agent_name, reason="Resumed conversation", result=result)

    elif step == "need_sudo_password":
        # User provided their sudo password
        sudo_password = user_answer.strip()
        if not sudo_password:
            return QueryResponse(agent=agent_name, reason="Cancelled", result="Deployment cancelled - no password provided.")
        
        # Store in prefill so it's passed to credentials and executor below
        prefill["sudo_password"] = sudo_password
        
        # Merge other context from agent_state
        prefill.update({k: v for k, v in agent_state.get("ctx_partial", {}).items() if k not in prefill or not prefill[k]})
        new_query = orig_request.get("query", "")

    elif step == "need_env":
        # User answered the .env question
        answer = user_answer.strip().lower()
        if answer in ("no", ""):
            # User doesn't need .env file
            prefill["env_vars"] = {}
        else:
            import json as _json

            def _parse_dotenv(text: str) -> dict:
                """
                Parse KEY=VALUE lines (raw .env format).
                - Strips blank lines and # comments
                - Handles optional surrounding quotes on values
                """
                result = {}
                for line in text.splitlines():
                    line = line.strip()
                    # Skip blanks and comment-only lines
                    if not line or line.startswith("#"):
                        continue
                    # Strip inline comments (e.g. KEY=value # comment)
                    if " #" in line:
                        line = line[:line.index(" #")].strip()
                    if "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key:
                        result[key] = val
                return result

            # Try JSON first, then fall back to .env line format
            try:
                prefill["env_vars"] = _json.loads(user_answer)
            except Exception:
                parsed = _parse_dotenv(user_answer)
                if parsed:
                    prefill["env_vars"] = parsed
                else:
                    logger.warning(f"[need_env] Could not parse env input, storing raw: {user_answer[:120]}")
                    prefill["env_vars"] = {}

        # Merge ctx_partial so early-exit path has all core fields
        prefill.update({k: v for k, v in agent_state.get("ctx_partial", {}).items() if k not in prefill or not prefill[k]})
        new_query = orig_request.get("query", "")

    elif step == "need_domain":
        answer = user_answer.strip()
        if not answer or answer.lower() in ("skip", "default", ""):
            # User skipped — use server default or _
            prefill["domain"] = agent_state.get("ctx_partial", {}).get("domain", "_")
        else:
            # User provided domain or IP
            prefill["domain"] = answer
        # Merge ctx_partial
        prefill.update({k: v for k, v in agent_state.get("ctx_partial", {}).items() if k not in prefill or not prefill[k]})
        new_query = orig_request.get("query", "")

    elif step == "need_nginx":
        answer = user_answer.strip().lower()
        if answer in ("no", "n", "skip"):
            prefill["nginx_choice"] = "no"
        elif answer in ("yes", "y"):
            prefill["nginx_choice"] = "yes"
        else:
            # Treat anything else as a custom domain/IP the user wants to use
            prefill["nginx_choice"] = user_answer.strip()
        # Merge ctx_partial so all core fields are available on resume
        prefill.update({k: v for k, v in agent_state.get("ctx_partial", {}).items() if k not in prefill or not prefill[k]})
        new_query = orig_request.get("query", "")

    elif step == "confirm_redeploy":
        # User confirmed redeploy from current branch
        answer = user_answer.strip().lower()
        if answer not in ("yes", "y", "proceed", "ok"):
            return QueryResponse(agent=agent_name, reason="Cancelled", result="Redeploy cancelled by user.")
        
        # User confirmed — prepare to redeploy from current branch
        app_path = agent_state.get("app_path", "")
        app_name = agent_state.get("app_name", "")
        current_branch = agent_state.get("current_branch", "main")
        
        logger.info(f"[REDEPLOY] User confirmed redeploy for {app_name} from branch {current_branch}")
        
        # Set prefill to skip LLM and go straight to redeploy
        # Load the last deployment context
        import json as _json
        try:
            from pathlib import Path
            deploy_ctx_file = Path(__file__).resolve().parent.parent.parent / "deployment_context.json"
            if deploy_ctx_file.exists():
                with open(deploy_ctx_file, "r") as f:
                    last_ctx = _json.load(f)
                
                # Build prefill from last deployment
                prefill["github_url"] = last_ctx.get("github_url", "")
                prefill["stack"] = last_ctx.get("stack", "")
                prefill["port"] = last_ctx.get("port", "")
                prefill["domain"] = last_ctx.get("domain", "_")
                prefill["process_manager"] = last_ctx.get("process_manager", "pm2")
                prefill["branch"] = current_branch
                prefill["app_path"] = app_path
                prefill["app_name"] = app_name
                prefill["redeploy"] = True  # Flag to skip clone and only pull + reinstall
                prefill["env_vars"] = {}  # Will be read from server if exists
                
                logger.info(f"[REDEPLOY] Loaded context: {prefill['github_url']} → {prefill['stack']}")
        except Exception as e:
            logger.error(f"[REDEPLOY] Could not load deployment context: {e}")
            return QueryResponse(
                agent=agent_name,
                reason="Error",
                result=f"Could not load previous deployment context: {e}"
            )
        
        new_query = orig_request.get("query", "")

    elif step == "choose_redeploy_branch":
        # User chose which branch to redeploy from (or confirm current)
        answer = user_answer.strip().lower()
        
        current_branch = agent_state.get("current_branch", "main")
        ctx_partial = agent_state.get("ctx_partial", {})
        
        # Determine which branch to use
        if answer in ("yes", "y", "ok", ""):
            # Use current branch
            branch_to_deploy = current_branch
        else:
            # User provided a different branch name
            branch_to_deploy = user_answer.strip()
        
        logger.info(f"[DEPLOY] User selected branch for redeploy: {branch_to_deploy}")
        
        # Mark as redeploy and set the branch to pull from
        prefill["redeploy"] = True
        prefill["current_branch"] = branch_to_deploy
        
        # Merge ctx_partial to have all deployment context
        prefill.update({k: v for k, v in ctx_partial.items() if k not in prefill or not prefill[k]})
        
        logger.info(f"[DEPLOY] Prefill after update: redeploy={prefill.get('redeploy')}, branch={prefill.get('current_branch')}, app_path={prefill.get('app_path')}")
        
        new_query = orig_request.get("query", "")

    elif step == "choose_same_folder_stack":
        # User chose which stack to deploy (multiple stacks in same folder)
        answer = user_answer.strip().lower()
        detected_stacks = agent_state.get("detected_stacks", {})
        
        # Parse user choice (numeric or name)
        selected_stack = None
        try:
            idx = int(answer) - 1
            stacks_list = list(detected_stacks.keys())
            if 0 <= idx < len(stacks_list):
                selected_stack = stacks_list[idx]
        except ValueError:
            # Try name match
            for stack_name in detected_stacks.keys():
                if answer in stack_name.lower() or stack_name.lower() in answer:
                    selected_stack = stack_name
                    break
        
        if not selected_stack:
            selected_stack = list(detected_stacks.keys())[0]  # Default to first
        
        logger.info(f"[DEPLOY] User selected stack: {selected_stack}")
        prefill["stack"] = selected_stack
        
        # Merge ctx_partial so deployment continues with this stack
        prefill.update({k: v for k, v in agent_state.get("ctx_partial", {}).items() if k not in prefill or not prefill[k]})
        prefill["deploy_steps_done"] = True  # Mark that clone completed
        new_query = orig_request.get("query", "")

    elif step == "choose_monorepo_parts":
        # User chose which part(s) of the monorepo to deploy
        answer = user_answer.strip().lower()
        detected_folders = agent_state.get("detected_folders", {})
        
        # Import deployment agent to reuse choice parsing logic
        from app.agents.deployment_agent import DeploymentAgent
        agent = DeploymentAgent(executor_type="local")  # Choice parsing is pure Python
        selected_parts = agent._parse_deployment_choice(answer, detected_folders)
        
        logger.info(f"[DEPLOY] User selected monorepo parts: {selected_parts}")
        
        if not selected_parts:
            selected_parts = list(detected_folders.keys())
        
        # Store detected folders and selected parts in prefill
        prefill["detected_folders"] = detected_folders
        prefill["selected_monorepo_parts"] = selected_parts
        prefill["deploy_monorepo"] = True
        
        # Merge ctx_partial so deployment continues
        prefill.update({k: v for k, v in agent_state.get("ctx_partial", {}).items() if k not in prefill or not prefill[k]})
        prefill["deploy_steps_done"] = True  # Mark that clone completed
        new_query = orig_request.get("query", "")

    elif step == "confirm_fresh_clone":
        answer = user_answer.strip().lower()
        if answer in ("yes", "y"):
            prefill["delete_confirm"] = "yes"
        else:
            prefill["delete_confirm"] = "no"
        # Merge ctx_partial so deployment can resume
        prefill.update({k: v for k, v in agent_state.get("ctx_partial", {}).items() if k not in prefill or not prefill[k]})
        new_query = orig_request.get("query", "")

    elif step == "cleanup_confirm":
        import json as _json
        answer = user_answer.strip().lower()
        if answer not in ("yes", "y"):
            return QueryResponse(agent=agent_name, reason="Cancelled", result="Cleanup cancelled.")
        raw_paths = agent_state.get("pending_paths", "[]")
        try:
            confirmed_paths = _json.loads(raw_paths)
        except Exception:
            confirmed_paths = []
        prefill["cleanup_confirmed_paths"] = confirmed_paths
        new_query = orig_request.get("query", "")

    elif step == "ops_confirm_command":
        pending_command = agent_state.get("pending_command", "")
        answer = user_answer.strip().lower()
        if answer in ("yes", "y"):
            prefill["confirmed_command"] = pending_command
        else:
            prefill["confirmed_command"] = None  # user declined — agent will skip it
        new_query = orig_request.get("query", "")

    else:
        # Unknown step — treat as extra info appended to query
        new_query = f"{orig_request.get('query', '')}. {user_answer}"

    # Rebuild request with updated prefill
    # IMPORTANT: Preserve credentials from current request if not in original
    creds = request.credentials
    if not creds and "request" in state:
        # Restore credentials from original request if stored
        orig_creds_dict = state.get("request", {}).get("credentials")
        if orig_creds_dict:
            from app.api.schemas import ServerCredentials
            creds = ServerCredentials(**orig_creds_dict)
            # Re-run validator to ensure sudo_password is loaded from .env
            creds = creds.model_validate(creds.model_dump())
    
    # CRITICAL: Always reload sudo_password from .env if not already set
    # This ensures sudo_password is available even when resuming conversations
    if creds and not creds.sudo_password:
        import os
        creds.sudo_password = os.getenv("SUDO_PASSWORD")
    
    # If user provided sudo_password in prefill, add it to credentials
    if prefill.get("sudo_password") and creds:
        creds.sudo_password = prefill["sudo_password"]
    
    executor_type   = creds.executor_type if creds else "local"
    executor_config = creds.to_config() if creds else {}
    label           = executor_config.get("host", "server")

    try:
        if agent_name == "setup":
            from app.agents.setup_agent import SetupAgent
            from app.api.schemas import SetupParams
            agent = SetupAgent(executor_type=executor_type, executor_config=executor_config, server_label=label)
            agent._prefill      = prefill
            agent._api_mode     = True
            agent._connected_as = executor_config.get("username", "root")  # preserve across resume turns
            result = agent.execute_task(new_query)

        elif agent_name == "deployment":
            from app.agents.deployment_agent import DeploymentAgent
            agent = DeploymentAgent(executor_type=executor_type, executor_config=executor_config)
            agent._prefill = prefill
            if prefill.get("github_token"):
                agent._github_token = prefill["github_token"]
            if prefill.get("nginx_choice") is not None:
                agent._nginx_choice = prefill["nginx_choice"]
            if prefill.get("delete_confirm") is not None:
                agent._delete_confirm = prefill["delete_confirm"]
            result = agent.execute_task(new_query)

        elif agent_name == "maintenance":
            from app.agents.maintenance_agent import MaintenanceAgent
            agent = MaintenanceAgent(executor_type=executor_type, executor_config=executor_config, server_label=label)
            if prefill.get("cleanup_confirmed_paths") is not None:
                result = agent._cleanup_remove(prefill["cleanup_confirmed_paths"])
            else:
                result = agent.execute_task(new_query)

        elif agent_name == "ops":
            from app.agents.ops_agent import OpsAgent
            agent = OpsAgent(executor_type=executor_type, executor_config=executor_config, server_label=label)
            if prefill.get("confirmed_command") is not None:
                agent._confirmed_command = prefill["confirmed_command"]
            elif prefill.get("confirmed_command") is None and step == "ops_confirm_command":
                # User declined — return cancellation message directly
                return QueryResponse(agent=agent_name, reason="Cancelled", result="Command skipped by user.")
            result = agent.execute_task(new_query)

        else:
            result = _dispatch(agent_name, new_query, request)
        return QueryResponse(agent=agent_name, reason="Resumed conversation", result=result)

    except NeedsInputError as nie:
        cid = conversation_store.save({
            "agent_name":  agent_name,
            "request":     orig_request,
            "question":    nie.question,
            "agent_state": {**nie.state, "prefill": prefill},
        })
        return QueryResponse(
            agent=agent_name,
            reason="Needs more information",
            result=None,
            needs_input=True,
            question=nie.question,
            conversation_id=cid,
        )
