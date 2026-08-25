# Changelog

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
