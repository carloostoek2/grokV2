"""Families in the application layer: ManageLists CRUD + coherent batch draws."""

from __future__ import annotations

from grokbot.application.events import EmptyList, ItemResult
from grokbot.application.generate_image import GenerateImageUseCase
from grokbot.application.job_manager import JobManager
from grokbot.application.manage_lists import ManageListsUseCase
from grokbot.application.run_variable_batch import RandomComboStrategy, RunVariableBatchUseCase
from grokbot.domain.families import Family

from conftest import USER_ID, FakeImageProvider, FakeVariablesRepo, make_registry


def _uc(sessions, reg, variables):
    return RunVariableBatchUseCase(
        sessions=sessions,
        registry=reg,
        variables=variables,
        job_manager=JobManager(),
        generate_image=GenerateImageUseCase(sessions=sessions, registry=reg),
    )


def _kinetic_family() -> Family:
    return Family(
        id="mirror",
        label="Mirror / Selfie",
        pools={
            "poses": ("sentada",),
            "angles": ("frontal",),
            "actions": ("sonriendo",),
        },
    )


# --- ManageListsUseCase ------------------------------------------------------
def test_save_family_validates_and_persists(variables_repo):
    uc = ManageListsUseCase(variables=variables_repo)
    ok, err = uc.save_family("mirror", {"label": "Mirror", "pools": {"poses": ["de pie"]}})
    assert ok is True and err is None
    family = variables_repo.get_family("mirror")
    assert family is not None and family.label == "Mirror"
    assert family.pools == {"poses": ("de pie",)}


def test_save_family_rejects_empty_pool_payload(variables_repo):
    uc = ManageListsUseCase(variables=variables_repo)
    ok, err = uc.save_family("mirror", {"label": "Mirror", "pools": {"poses": []}})
    assert ok is False
    assert err == "'poses' debe ser una lista de valores no vacía."
    assert variables_repo.get_family("mirror") is None


def test_save_family_rejects_non_dict_pools(variables_repo):
    uc = ManageListsUseCase(variables=variables_repo)
    ok, err = uc.save_family("mirror", {"label": "Mirror", "pools": ["de pie"]})
    assert ok is False
    assert err == "'pools' debe ser un objeto {campo: [valores]}."


def test_save_family_rejects_unknown_value(variables_repo):
    uc = ManageListsUseCase(variables=variables_repo)
    ok, err = uc.save_family("mirror", {"label": "Mirror", "pools": {"poses": ["volando"]}})
    assert ok is False
    assert err == "El valor 'volando' no existe en la lista 'poses'."
    assert variables_repo.get_family("mirror") is None


def test_save_family_rejects_unknown_field(variables_repo):
    uc = ManageListsUseCase(variables=variables_repo)
    ok, err = uc.save_family("mirror", {"label": "Mirror", "pools": {"camara": ["frontal"]}})
    assert ok is False
    assert err == "El campo 'camara' no existe en las listas activas."


def test_activate_and_delete_family(variables_repo):
    uc = ManageListsUseCase(variables=variables_repo)
    uc.save_family("mirror", {"label": "Mirror", "pools": {"poses": ["de pie"]}})
    ok, err = uc.activate_family("mirror")
    assert ok is True and err is None
    assert uc.active_family_name() == "mirror"
    assert uc.active_family().label == "Mirror"
    # The active family cannot be deleted; deactivating unlocks it.
    assert uc.delete_family("mirror") is False
    ok, err = uc.activate_family(None)
    assert ok is True and err is None
    assert uc.active_family_name() is None
    assert uc.delete_family("mirror") is True


def test_activate_family_rejects_a_stale_family():
    family = Family(id="mirror", label="Mirror", pools={"poses": ("de pie",)})
    repo = FakeVariablesRepo(lists={"bodies": ["x"]}, families={"mirror": family})
    uc = ManageListsUseCase(variables=repo)

    ok, err = uc.activate_family("mirror")

    assert ok is False
    assert err == (
        "La familia no encaja con las listas activas: "
        "El campo 'poses' no existe en las listas activas."
    )
    assert uc.active_family_name() is None


# --- RandomComboStrategy / batch ---------------------------------------------
async def test_random_strategy_draws_only_from_family_pool(sessions, variables_repo, fast_sleep):
    family = _kinetic_family()
    provider = FakeImageProvider(name="kie")
    reg = make_registry(kie=provider)
    uc = _uc(sessions, reg, variables_repo)

    events = [
        ev
        async for ev in uc.run(
            user_id=USER_ID,
            count=1,
            strategy=RandomComboStrategy(variables_repo, family=family),
            family=family,
        )
    ]

    assert isinstance(events[0], ItemResult) or any(isinstance(ev, ItemResult) for ev in events)
    result = next(ev for ev in events if isinstance(ev, ItemResult))
    assert result.prompt == "sentada, frontal, sonriendo"
    assert provider.calls[0][0].prompt == "sentada, frontal, sonriendo"


async def test_random_strategy_honours_avoid(sessions, variables_repo, fast_sleep):
    family = Family(id="no_smile", label="No smile", avoid={"actions": ("sonriendo",)})
    provider = FakeImageProvider(name="kie")
    reg = make_registry(kie=provider)
    uc = _uc(sessions, reg, variables_repo)

    async for _ev in uc.run(
        user_id=USER_ID,
        count=1,
        strategy=RandomComboStrategy(variables_repo, family=family),
        family=family,
    ):
        pass

    assert provider.calls[0][0].prompt.endswith("mirando a cámara")


async def test_empty_effective_field_aborts_before_job(sessions, variables_repo, fast_sleep):
    family = Family(id="no_pose", label="No pose", pools={"poses": ()})
    uc = _uc(sessions, make_registry(), variables_repo)

    events = [
        ev
        async for ev in uc.run(
            user_id=USER_ID,
            count=1,
            strategy=RandomComboStrategy(variables_repo, family=family),
            family=family,
        )
    ]

    assert len(events) == 1
    assert isinstance(events[0], EmptyList)
    assert events[0].name == "poses"


async def test_avoid_covering_every_value_aborts_before_job(sessions, variables_repo, fast_sleep):
    family = Family(
        id="quiet",
        label="Quiet",
        avoid={"actions": ("mirando a cámara", "sonriendo")},
    )
    uc = _uc(sessions, make_registry(), variables_repo)

    events = [
        ev
        async for ev in uc.run(
            user_id=USER_ID,
            count=1,
            strategy=RandomComboStrategy(variables_repo, family=family),
            family=family,
        )
    ]

    assert len(events) == 1
    assert isinstance(events[0], EmptyList)
    assert events[0].name == "actions"


async def test_unreferenced_empty_list_does_not_abort(sessions, fast_sleep):
    variables = FakeVariablesRepo(
        lists={
            "poses": ["de pie"],
            "angles": ["frontal"],
            "actions": ["mirando a cámara"],
            "outfits": [],
        },
        template="{pose}, {angle}",
    )
    provider = FakeImageProvider(name="kie")
    uc = _uc(sessions, make_registry(kie=provider), variables)

    events = [
        ev
        async for ev in uc.run(
            user_id=USER_ID,
            count=1,
            strategy=RandomComboStrategy(variables),
        )
    ]

    assert any(isinstance(ev, ItemResult) for ev in events)
    assert provider.calls[0][0].prompt == "de pie, frontal"


async def test_batch_uses_active_family_via_repository_state(sessions, variables_repo, fast_sleep):
    """The handler resolves the active family; the batch simply receives it."""
    family = _kinetic_family()
    variables_repo.save_family(family)
    variables_repo.set_active_family("mirror")
    active = variables_repo.get_family(variables_repo.active_family_name())

    provider = FakeImageProvider(name="kie")
    uc = _uc(sessions, make_registry(kie=provider), variables_repo)
    async for _ev in uc.run(
        user_id=USER_ID,
        count=1,
        strategy=RandomComboStrategy(variables_repo, family=active),
        family=active,
    ):
        pass

    assert provider.calls[0][0].prompt == "sentada, frontal, sonriendo"
