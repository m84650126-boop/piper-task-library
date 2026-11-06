"""Named task variants reuse proven base physics/controllers without relabeling skill evidence."""
from .plugin_api import TaskPlugin


class VariantEnvironment:
    def __init__(self,env,task):
        object.__setattr__(self,"_inner",env)
        object.__setattr__(self,"task_name",task)

    def __getattr__(self,name):return getattr(self._inner,name)

    def __setattr__(self,name,value):
        if name in ("_inner","task_name"):object.__setattr__(self,name,value)
        else:setattr(self._inner,name,value)


def build_variant(config):
    from .tasks import TASKS,check_task_condition,evaluate_task,SURFACE_EXTRA,WIPE_EXTRA
    base=config["base_task"];task=config["id"]
    if base not in ("pick_place","push","pull","wipe"):raise ValueError("Unsupported built-in task template")
    def make_environment(recipe,render,recorded_xml):
        from .core import make_environment as make_base
        env=make_base(render,base,recorded_xml,{**recipe,"task":base})
        env.recipe_profile=recipe
        return VariantEnvironment(env,task)
    def preflight(env):
        from .core import preflight as plan
        return plan(env._inner)
    def expert(env,plan):
        from .core import expert as generate
        yield from generate(env._inner,plan)
    def evaluate(samples,stages,start,goal):
        result=dict(evaluate_task(samples,stages,base,start,goal))
        success=bool(result.get("success",result.get("pick_place_success",False)))
        result.update(execution_unit=task,base_execution_unit=base,success=success,
            independent_learned_policy_validated=False)
        if "checks" not in result:result["checks"]={"complete_pick_place_contract":success}
        return result
    def prepare_recording(env):
        if base=="wipe":
            if env.pad_contact()>.05:raise ValueError("请先抬离擦拭工具，再开始完整擦拭记录")
            env.clear_wipe()
        else:
            contacts,table=env.contact_evidence()
            if contacts.any() or not table:raise ValueError("请从尚未抓持、桌面承载的状态开始录制")
    return TaskPlugin(id=task,version=config["version"],label=config["label"],text=config["text"],
        hint="基于"+TASKS[base]["label"]+"的配置变体；"+TASKS[base]["hint"],
        phases=TASKS[base]["phases"],preset=config["preset"],base_task=base,
        evidence_keys=tuple(SURFACE_EXTRA+(WIPE_EXTRA if base=="wipe" else ())) if base!="pick_place" else (),
        make_environment=make_environment,check_condition=lambda start,goal:check_task_condition(base,start,goal),
        preflight=preflight,expert=expert,evaluate=evaluate,prepare_recording=prepare_recording)
