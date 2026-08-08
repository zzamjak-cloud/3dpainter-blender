"""클래스 등록 목록 자동 수집.

수동 `classes = (...)` 튜플은 새 클래스를 추가할 때 갱신을 잊으면 조용히 등록이
누락된다. 모듈 네임스페이스를 정의 순서대로 훑어 등록 대상을 모은다.
"""

import bpy

# 등록 가능한 bpy 기반 타입. bl_parent_id/PointerProperty 의존 때문에
# 수집 순서는 반드시 "모듈 내 정의 순서"여야 한다.
_REGISTRABLE_BASES = (
    bpy.types.Panel,
    bpy.types.Menu,
    bpy.types.UIList,
    bpy.types.Operator,
    bpy.types.PropertyGroup,
    bpy.types.AddonPreferences,
)


def collect_classes(module):
    """`module`에 정의된 등록 대상 클래스를 정의 순서대로 담은 튜플을 돌려준다.

    - 다른 모듈에서 import해 온 클래스(믹스인 등)는 `__module__` 비교로 걸러낸다.
    - `_ps_skip_register = True`가 붙은 클래스는 제외한다.
    """
    module_name = module.__name__
    return tuple(
        obj
        for obj in vars(module).values()
        # 파이썬 dict는 삽입 순서를 보존하므로 vars() 순회 = 정의 순서
        if isinstance(obj, type)
        and issubclass(obj, _REGISTRABLE_BASES)
        and obj.__module__ == module_name
        # 마커를 믹스인에 붙였을 때 서브클래스까지 조용히 빠지는 것을 막기 위해
        # 상속된 값이 아닌 클래스 자신의 속성만 본다
        and not vars(obj).get("_ps_skip_register", False)
    )
