# Changelog

## [0.7.0](https://github.com/pythoninthegrass/burnkit/compare/v0.6.1...v0.7.0) (2026-09-03)


### ⚠ BREAKING CHANGES

* **queue:** load_attempts returns dict[str, Attempt] rather than dict[str, int], and bump_attempts takes the task fingerprint. Attempt counts already on disk are bare ints and still load, as an unknown definition.

### Features

* **queue:** let a revised task out of triage, and never queue an archived one ([0ee4a9f](https://github.com/pythoninthegrass/burnkit/commit/0ee4a9f723548fb302c112ad8179aff21ce74f19))


### Bug Fixes

* **proc:** ignore an exit marker left by an earlier attempt at the same log path ([a9c4c2b](https://github.com/pythoninthegrass/burnkit/commit/a9c4c2bdd8c0b61969edb3c4c7ea6e64d3393941)), closes [#8](https://github.com/pythoninthegrass/burnkit/issues/8)
* **proc:** send SIGTERM before SIGKILL when killing a task's process group ([3c746e4](https://github.com/pythoninthegrass/burnkit/commit/3c746e42a58b3380acc7dbdd313b7f95a0502983))

## [0.6.1](https://github.com/pythoninthegrass/burnkit/compare/v0.6.0...v0.6.1) (2026-08-25)


### Bug Fixes

* **backend:** link out-of-tree children when git already made the directory ([cfd5abc](https://github.com/pythoninthegrass/burnkit/commit/cfd5abc7f82fbea3f45ef6774bb6e1a3b3150d19))
* **cli:** free a killed attempt's branch so resume can relaunch it ([9a2ceac](https://github.com/pythoninthegrass/burnkit/commit/9a2ceac18eae388795c97bb0617fafbf855bacf7))

## [0.6.0](https://github.com/pythoninthegrass/burnkit/compare/v0.5.0...v0.6.0) (2026-08-25)


### Features

* stop counting gates that verified nothing as evidence ([e7f3757](https://github.com/pythoninthegrass/burnkit/commit/e7f3757ba8d747b86980c5180e5915b40e91ecd3))

## [0.5.0](https://github.com/pythoninthegrass/burnkit/compare/v0.4.0...v0.5.0) (2026-08-25)


### ⚠ BREAKING CHANGES

* `Backend.stall_check` is now `Callable[[str, Path, float], str | None]`, taking the attempt's launch time as a third argument. `cli.stall_watch` passes three arguments unconditionally, so a custom Backend supplying a two-argument `stall_check` raises TypeError on the first poll. Add the parameter; ignore it to keep the old behavior.

### Features

* scope stall detection to the current attempt's session log ([cabcf6a](https://github.com/pythoninthegrass/burnkit/commit/cabcf6afde73e4757794270e87a73ab9408fa95d))

## [0.4.0](https://github.com/pythoninthegrass/burnkit/compare/v0.3.1...v0.4.0) (2026-08-24)


### Features

* end a run whose tool calls stop making progress ([e578e74](https://github.com/pythoninthegrass/burnkit/commit/e578e7498c771eff1f6622afdf8ff55d7547e8fe))


### Documentation

* expand burn-dsh-log README with subcommand reference and workflow ([7cb9cd7](https://github.com/pythoninthegrass/burnkit/commit/7cb9cd730318dd4fdff775df6c33fe0ce13a38af))

## [0.3.1](https://github.com/pythoninthegrass/burnkit/compare/v0.3.0...v0.3.1) (2026-08-24)


### Documentation

* update readme ([e8f8739](https://github.com/pythoninthegrass/burnkit/commit/e8f8739efb7ec1ba7185c53335e91f99abe6c98f))
