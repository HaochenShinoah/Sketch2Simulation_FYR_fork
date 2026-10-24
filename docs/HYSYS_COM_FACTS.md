# HYSYS COM facts behind the template and the instruction files

Record of what is verified on **Aspen HYSYS V11** (ProgramBuild 110395, Windows, pywin32 late-bound `win32com.client.DispatchEx("HYSYS.Application")`) on **2026-10-22**. It replaces the earlier version of this file, which had been compiled from public code without a HYSYS installation. The template helpers (`TYPES`, `PORTS`, `COLUMN_TEMPLATES` in `sketch2sim/instructions/hysys_template_base.py`) carry exactly the strings and members listed here; `connect`, `connect_column_feed` and `connect_column_product` read every attachment back from `AttachedFeeds.Names` / `AttachedProducts.Names`, because several members are accepted by HYSYS without attaching anything (sections 2.4, 3.4).

Target call shapes, as in the template: `flowsheet.Operations.Add(name, type)`, `flowsheet.MaterialStreams.Add(name)`, `units[k].<member> = streams[s]` (property) or `units[k].<collection>.Add(streams[s])` (collection).

Confidence legend: **verified** = executed live on V11 with the stated result; **negative** = executed live and failed, or was accepted and had no effect; **untested** = not executed (listed only where the distinction matters for the helpers). Nothing below is inferred from documentation or third-party code.

Sources (abbreviations used in the tables):

- **DISC** = `logs/hysys_com_discovery.md` / `.json`: the run of `tools/hysys_discover_com_api.py` on 2026-10-22 11:17 (build 110395; one new `AspenHysys.exe`, `SimulationCases.Add` on the first try, `Quit()` confirmed by PID). It creates every template category, exercises every port member of the helper's `PORTS` table **with read-back through `AttachedFeeds` / `AttachedProducts`**, loads the column template of all six column categories and exercises their feed, draw and product members, and lists the stream-port members of every created object. The earlier run of the same day (04:32, member lists without read-back) is kept as `logs/hysys_com_discovery_0432.*`.
- **EXP1 … EXP6** = `col_exp_report.json`, `col_exp2_report.json`, `col_exp3_report.json`, `col_exp4_report.json`, `col_exp5_report.json`, `col_exp6_report.json`: step logs written by `col_exp.py` … `col_exp6.py` in the scratch directory `%LOCALAPPDATA%\Temp\s2s_exp` (kept with the change plan's evidence; not part of the repository). "step n" refers to the `=== n. …` headings inside each file.
- **QUIT** = `quit_test_report.json` (`quit_test.py`).
- **REPLAY** = `replay_out/replay_cs3.log`, `replay_out/replay_cs41.log`: the agents' first-attempt cs3 and cs41 scripts re-run with the prototype helper block (`helpers_v6.py`, `replay.py`).
- **INSPECT** = `inspect_report.json` (the two replayed cases reopened through COM with `SimulationCases.Open`) and `inspect_report_cs1_cs2.json` (`outputs/cs1/Crude_Oil_Desalting.hsc` and `outputs/cs2/Jet Fuel Merox Sweetening.hsc` reopened).
- **LOGS** = the executor logs of the pipeline runs of the same day in `logs/cs3`, `logs/cs31`, `logs/cs4`, `logs/cs41`.

Error texts in the reports are localized. `'发生意外。'` is "Unexpected error" with the HRESULT in the tuple: `-2147467259` E_FAIL, `-2147024809` E_INVALIDARG; `-2147023170` is "RPC call failed"; `'找不到成员。'` is "Member not found"; `'未知名称。'` is "Unknown name".

---

## 1. Creation: `flowsheet.Operations.Add(name, type_string)`

The call shape `Add(name, type)` works for every type string below (DISC: "OK via Add(name, type)" for all categories; no other shape was needed). The returned object's `TypeName` equals the type string. `VisibleTypeName` is the palette name (the 04:32 run printed some of them in Chinese, the 11:17 run and the experiments in English; the English names are given).

| category (`TYPES` key) | type string = TypeName | VisibleTypeName | source |
|---|---|---|---|
| pump | `pumpop` | Pump | DISC; REPLAY, INSPECT (cs1 pumps) |
| compressor | `compressor` | Compressor | DISC; REPLAY cs41 |
| expander | `expandop` | Expander | DISC; EXP1 step 7 |
| valve | `valveop` | Valve | DISC; EXP1 step 7; INSPECT cs1 |
| mixer | `mixerop` | Mixer | DISC; EXP1 step 7; REPLAY cs3 |
| tee | `teeop` | Tee | DISC |
| heater | `heaterop` | Heater | DISC; REPLAY cs3, cs41 |
| cooler | `coolerop` | Cooler | DISC; REPLAY cs41 |
| heat_exchanger | `heatexop` | Heat Exchanger | DISC; EXP1 step 7 |
| air_cooler | `aircoolerop` | Air cooler | DISC; EXP1 step 7 |
| fired_heater | `dynamicfiredheaterop` | Fired Heater | DISC; EXP1 step 6; REPLAY cs3, cs41 |
| separator | `flashtank` | Separator | DISC; EXP1 step 7; INSPECT cs1, cs2 |
| separator_3phase | `sep3op` | 3 Phase Separator | EXP2 step C; EXP3 step 6; EXP5 step 4 |
| tank | `tankop` | Tank | DISC; EXP1 step 7; REPLAY cs3, cs41 |
| reactor_conversion | `conversionreactorop` | Conversion Reactor | DISC; REPLAY cs41; INSPECT cs2 |
| reactor_equilibrium | `equilibriumreactorop` | Equilibrium Reactor | DISC; EXP1 step 7 |
| reactor_gibbs | `gibbsreactorop` | Gibbs Reactor | DISC; EXP1 step 7 |
| reactor_cstr | `kineticreactorop` | Cont. Stirred Tank Reactor | EXP2 step C |
| reactor_pfr | `pfreactorop` | Plug Flow Reactor | DISC; EXP2 step C |
| column_distillation | `distillation` | Distillation | DISC; EXP1 step 1; EXP3; REPLAY |
| column_absorber | `absorber` | Absorber | DISC; EXP1 step 5; EXP4 step 6 |
| column_reboiled_absorber | `reboiledabsorber` | Reboiled Absorber | DISC; EXP1 step 5; EXP4 step 6 |
| column_refluxed_absorber | `refluxedabsorber` | Refluxed Absorber | DISC; EXP1 step 5; EXP4 step 6 |
| column_3phase | `threephase` | Three Phase Distillation | EXP2 step C; EXP4 step 6; EXP6 step 2 |
| column_extractor | `liqliqextractor` | Liquid-Liquid Extractor | EXP1 step 5; EXP6 step 3 |
| (no TYPES category; shortcut columns map to column_distillation) | `shdistop` | Shortcut Column | DISC; EXP1 step 5 (no `ColumnFlowsheet`, see 2.7) |
| component_splitter | `fractop` | Component Splitter | DISC; EXP1 step 7 |
| recycle | `recycle` | Recycle | DISC |
| adjust | `adjust` | Adjust | DISC |
| set | `setop` | Set | DISC |
| spreadsheet | `spreadsheetop` | Spreadsheet | DISC |
| pipe | `pipeseg` | Pipe Segment | DISC; EXP1 step 7 |

Verified type strings the agents do not use: `lngop` (LNG), `balanceop` (Balance), `reliefvalveop` (Relief valve), `streamcutterop` (Stream Cutter) (DISC; EXP1 step 7). `ColumnOp` is accepted too and creates a `columnop` "Column Sub-Flowsheet" whose `ColumnFlowsheet` raises E_FAIL (EXP1 step 5); it is not a usable column.

Type matching is case-insensitive for the strings above (`ColumnOp` -> `columnop`, `TraySection` -> `traysection`; EXP1 step 5, EXP3 step 3), but the class-style spellings `CompressOp` and `HeatExchanger` were never tried and were removed from the template as unneeded (untested).

---

## 2. Connections per unit family

Assign the stream **object** for a property member (`op.Member = stream`) and call `op.Collection.Add(stream)` for a collection member. The truth about an attachment is `op.AttachedFeeds.Names` / `op.AttachedProducts.Names` (and `stream.UpstreamOpers` / `stream.DownstreamOpers`, INSPECT). Attachments made through COM persist in the saved `.hsc`: INSPECT lists every attachment of the two replayed cases and of cs1/cs2 after reopening. A role whose name ends in `_in` is read back in `AttachedFeeds`, every other role in `AttachedProducts`.

| family (`PORTS` roles) | type strings | inlet members | outlet members | read-back behaviour | source |
|---|---|---|---|---|---|
| single-port (`single_in`, `single_out`) | `pumpop`, `compressor`, `expandop`, `valveop`, `heaterop`, `coolerop`, `aircoolerop`, `recycle`, `pipeseg` | `FeedStream` (set) | `ProductStream` (set) | attached, listed in `AttachedFeeds` / `AttachedProducts` | DISC; REPLAY `[verify]` lines; INSPECT |
| energy (helper `connect_energy`) | `pumpop`, `compressor`, `expandop`, `heaterop`, `coolerop`, `pipeseg`, vessels, reactors | `EnergyStream` (set, a stream from `flowsheet.EnergyStreams.Add(name)`) | — | attached; `EnergyStream` reads back the stream | DISC (`set_energy:EnergyStream` attached on every unit in the list, the CSTR and the PFR included). `valveop`, `aircoolerop`, `recycle` have no `EnergyStream` member (DISC: `set_energy` fails on them); `dynamicfiredheaterop` has none either (DISC member lists) |
| mixer (`multi_in`, `mixer_out`) | `mixerop` | `Feeds.Add` | `Product` (set) | attached | DISC; REPLAY cs3 (`Mixer: feeds=['Water', 'S102'] products=['Crude-Water Mixture']`); INSPECT cs1 |
| tee (`tee_in`, `tee_out`) | `teeop` | `FeedStream` (set) | `Products.Add` | attached | DISC. The tee has no `Feed` member; `Feeds` is read-only (DISC member list) |
| 2-phase vessel (`multi_in`, `vapour_out`, `liquid_out`) | `flashtank`, `tankop` | `Feeds.Add` | `VapourProduct`, `LiquidProduct` (set) | attached | DISC; REPLAY; INSPECT cs1 (`Electrostatic separator: products=['Effluent Water', 'Desalted crude oil']`) |
| 2-phase vessel, `heavy_liquid_out` | `flashtank`, `tankop` | — | `HeavyLiquidProduct` (set) | **accepted but not attached** (2.4) | DISC ("accepted but NOT attached" on both); EXP1 step 7; INSPECT cs2 |
| 3-phase separator (`multi_in`, `vapour_out`, `light_liquid_out`, `heavy_liquid_out`) | `sep3op` | `Feeds.Add` | `VapourProduct`, `LiquidProduct` (= light liquid), `HeavyLiquidProduct` (set) | attached | EXP3 step 6; EXP5 step 4 |
| reactors (`multi_in`, `vapour_out`, `liquid_out`) | `conversionreactorop`, `equilibriumreactorop`, `gibbsreactorop` | `Feeds.Add` | `VapourProduct`, `LiquidProduct` (set) | attached; `HeavyLiquidProduct` raises (not a silent no-op as on the vessels) | DISC; REPLAY cs41 (`R-101.VapourProduct`); INSPECT cs2 (reactor with three feeds, one product) |
| CSTR (`multi_in`, `vapour_out`, `liquid_out`) | `kineticreactorop` | `Feeds.Add` | `VapourProduct`, `LiquidProduct` (set) | attached; `EnergyStream` attached; `HeavyLiquidProduct` raises | DISC; EXP2 step C (creation) |
| PFR (`multi_in`, `pfr_out`) | `pfreactorop` | `Feeds.Add` | `Product` (set). `VapourProduct` / `LiquidProduct` do not exist on the PFR | attached (`Feeds.Add`, `Product`, `EnergyStream`) | DISC |
| heat exchanger (`tube_in`, `tube_out`, `shell_in`, `shell_out`) | `heatexop` | `TubeSideFeed`, `ShellSideFeed` (set) | `TubeSideProduct`, `ShellSideProduct` (set) | attached | DISC |
| component splitter (`splitter_in`, `splitter_overhead`, `splitter_bottom`) | `fractop` | `Inlets.Add` | `OverheadOutlets.Add`, `BottomOutlets` (set) | attached | DISC |
| fired heater (`fired_in`, `fired_out`, `flue_gas_out`, `fuel_in`) | `dynamicfiredheaterop` | `RadInlet.Add`, `BurnerFeed` (set) | `RadOutlet.Add`, `CombustionProduct` (set) | attached; see section 4 | EXP1 step 6; EXP3 step 5; EXP5 step 3; REPLAY; INSPECT |
| columns | `distillation`, `absorber`, `reboiledabsorber`, `refluxedabsorber`, `threephase`, `liqliqextractor` | see section 3 | see section 3 | read back on the column object | EXP3–EXP6; REPLAY; INSPECT |
| logical | `adjust`, `setop`, `spreadsheetop` | no stream-port members besides the read-only `AttachedFeeds` / `AttachedProducts` / `AttachedLogicalOps` | — | — | DISC member lists |

### 2.1 Single-port units

`FeedStream` / `ProductStream` take the stream object and the stream appears in `AttachedFeeds` / `AttachedProducts` at once (DISC; REPLAY `[connect] FEED_PUMP.FeedStream <- CRUDE_OIL` … `[verify] FEED_PUMP: feeds=['CRUDE_OIL'] products=['S101']`). Both members are single-valued (one stream each, DISC member list `get/put`).

### 2.2 Mixer and tee

Mixer: `Feeds.Add(stream)` for every inlet, `Product = stream` (DISC; REPLAY cs3; INSPECT cs1 mixer with three feeds). Tee: `FeedStream = stream`, `Products.Add(stream)` for every outlet (DISC). `Feeds` on the tee and `Products` on the mixer are read-only collections (DISC member lists: `Feeds (get)`, `Products (get)`).

### 2.3 Heat exchanger

`heatexop`: `TubeSideFeed`, `TubeSideProduct`, `ShellSideFeed`, `ShellSideProduct` (set), all attached (DISC). `lngop` exposes no stream-port member in its type information and the four `heatexop` members fail on it (DISC).

### 2.4 Two-phase vessels and the silent `HeavyLiquidProduct`

`flashtank` (Separator) and `tankop` (Tank): `Feeds.Add`, `VapourProduct`, `LiquidProduct`, `EnergyStream` (DISC; REPLAY; INSPECT). Both also expose `HeavyLiquidProduct (get/put)` (DISC member list). On both 2-phase vessels the assignment is **accepted without error and attaches nothing** (DISC: `set:HeavyLiquidProduct` "accepted but NOT attached" on `flashtank` and on `tankop`): after `VapourProduct = 'Sep Vap'`, `LiquidProduct = 'Sep Light'`, `HeavyLiquidProduct = 'Sep Heavy'` the products are still `['Sep Light', 'Sep Vap']` (EXP1 step 7, "flashtank HeavyLiquidProduct silent-noop check"). The cs2 case shows the consequence: the caustic settler was built as `flashtank`, its heavy liquid was assigned to `HeavyLiquidProduct`, and the reopened case lists the settler with the single product `Hydrocarbon phase` while the caustic recycle is attached only at the pump (INSPECT `inspect_report_cs1_cs2.json`). `heavy_liquid_out` is therefore only meaningful on `separator_3phase`; on a 2-phase vessel the helper's read-back reports it as a failure.

### 2.5 Reactors

`conversionreactorop`, `equilibriumreactorop`, `gibbsreactorop`: `Feeds.Add`, `VapourProduct`, `LiquidProduct`, `EnergyStream` (DISC; REPLAY cs41 `R-101.Feeds.Add <- S6`, `R-101.VapourProduct <- S7`; INSPECT cs2 Merox reactor). `kineticreactorop` (CSTR): `Feeds.Add`, `VapourProduct`, `LiquidProduct`, `EnergyStream` attached (DISC; created in EXP2 step C). `pfreactorop` (PFR): `Feeds.Add`, `Product` and `EnergyStream` attached (DISC); its only outlet member is `Product (get/put)`, there is no `VapourProduct` / `LiquidProduct` (DISC member list; setting `Product` untested).

### 2.6 Component splitter

`fractop`: `Inlets.Add`, `OverheadOutlets.Add`, `BottomOutlets = stream` (DISC). `EnergyInlets` is a read-only collection (DISC member list).

### 2.7 Logical operations and the shortcut column

`adjust`, `setop`, `spreadsheetop`: no stream-port members (DISC). `recycle`: `FeedStream` / `ProductStream` attached, no `EnergyStream` (DISC). `shdistop` (Shortcut Column): only the read-only `AttachedFeeds` / `AttachedProducts` / `AttachedLogicalOps` in its type information (DISC) and no `ColumnFlowsheet` (EXP1 step 5): there is no verified way to attach streams to it, so `column_shortcut` is kept in `TYPES` as a plain unit only. `streamcutterop` lists `Inlet` / `Outlet (get/put)` (DISC); untested.

---

## 3. Columns

Applies to `distillation`, `absorber`, `reboiledabsorber`, `refluxedabsorber`, `threephase`, `liqliqextractor`.

### 3.1 Creation recipe

1. `col = flowsheet.Operations.Add(name, type)` creates an **empty shell**: `col.ColumnFlowsheet` raises E_FAIL (EXP1 step 1; EXP3 step 2 with the solver active; LOGS: every column connection of cs3, cs31, cs4 and cs41 failed with this error, which is what their saved cases contain).
2. `col.LoadColumnTemplate(template)` installs the sub-flowsheet (tray section, condenser, reboiler as applicable). Verified calls: `distillation` with `"distil"`, `"distil.col"`, the full path `C:\Program Files\AspenTech\Aspen HYSYS V11.0\Template\distil.col`, `"Distillation"` and no argument (EXP3 step 1); `absorber` with `"absorber"` (EXP4 step 6); `threephase` with `"3phase"` (EXP4 step 6; EXP6 step 2); `reboiledabsorber`, `refluxedabsorber` and `liqliqextractor` with no argument (EXP4 step 6; EXP6 step 3). The no-argument form loads the type's default template. The template files are `Template\distil.col`, `absorber.col`, `rebabs.col`, `refabs.col`, `3phase.col`, `liqliqex.col` (EXP2 step A2 and EXP4 step 6 directory listings); the helper passes the file stems `distil / absorber / rebabs / refabs / 3phase / liqliqex` (`COLUMN_TEMPLATES`). `rebabs`, `refabs` and `liqliqex` as explicit names were not run (untested; the no-argument form of those three types was). Loading works with the solver held or active (EXP3 steps 1 and 2). `EnterColumnEnvironment()` is not needed: the sub-flowsheet is the same before and after it (EXP3 step 1).
3. **Never call `CreateColumnTemplate()` first.** It creates an empty sub-flowsheet (`Operations = []`) and makes every later `LoadColumnTemplate(...)` a no-op: 44 names and paths, among them every `.col` file, all returned "OK" and left `[]` (EXP2 steps A1 and A2; EXP1 step 1). Building the sub-flowsheet by hand is a dead end: `cf.Operations.Add("Main TS", "traysection")` works, but no type string creates a condenser or reboiler (`condenser`, `condenserop`, `reboiler`, `reboilerop` fail; EXP3 step 3).

### 3.2 Sub-flowsheet after the template (`col.ColumnFlowsheet.Operations`, name: TypeName)

| template | operations | internal streams | source |
|---|---|---|---|
| `distil` | `Main Tower`: `traysection` (`NumberOfStages` 10); `Condenser`: `partialcondenser`; `Reboiler`: `bpreboiler`; `ProductBlock_Ovhd Vapour`, `ProductBlock_Distillate`, `ProductBlock_Btms`: `feederblock` | `Reflux`, `To Condenser`, `Boilup`, `To Reboiler`, `Ovhd Vapour`, `Distillate`, `Btms`; energy `Q-Condenser`, `Q-Reboiler` (listed in `FeedStreams`) | EXP3 steps 1 and 4; REPLAY `[unit] … template [...]` |
| `absorber` | `Main Tower`; `FeederBlock_Liquid Feed`, `FeederBlock_Vapour Feed`, `ProductBlock_Vapour Product`, `ProductBlock_Liquid Product` | `Liquid Feed`, `Vapour Feed`, `Vapour Product`, `Liquid Product` | EXP4 step 6 |
| `rebabs` | `Main Tower`; `Reboiler`: `bpreboiler`; `ProductBlock_Ovhd Product`, `ProductBlock_Btms Product` | `Ovhd Product`, `To Reboiler`, `Boilup`, `Btms Product` | EXP4 step 6 |
| `refabs` | `Condenser`: `partialcondenser`; `Main Tower`; `ProductBlock_Btms Product`, `ProductBlock_Distillate`, `ProductBlock_Vap Product` | `Reflux`, `To Condenser`, `Btms Product`, `Distillate`, `Vap Product` | EXP4 step 6 |
| `3phase` | `Condenser`: `condenser3chemicalop`; **`Three Phase Column`**: `traysection`; `Reboiler`: `bpreboiler`; `ProductBlock_Vent`, `ProductBlock_Aquaeous Product`, `ProductBlock_Organic Product`, `ProductBlock_Btms Product` | `To Condenser`, `Vent`, `Aquaeous Product`, `Organic Product`, `Organic Reflux`, `To Reboiler`, `Boilup`, `Btms Product`, `Heavy Reflux` | EXP4 step 6; EXP6 step 2 |
| `liqliqex` | `Main Tower`; `FeederBlock_Liquid 1 In`, `FeederBlock_Liquid 2 In`, `ProductBlock_Light Liquid Product`, `ProductBlock_Heavy Liquid Product` | `Liquid 1 In`, `Liquid 2 In`, `Light Liquid Product`, `Heavy Liquid Product` | EXP6 step 3 |

The tray section is found by `TypeName` containing `tray`, not by name: it is `Main Tower` on five templates and `Three Phase Column` on the 3-phase one (DISC lists the operations of all six loaded templates). DISC exercised `LoadColumnTemplate()`, `AddFeedStream(name, 5, False)`, `AddDrawStream(name, 1, 5, True, False)`, `TopLiquidFeed`, `BtmVapourFeed` and the four product members on every one of the six templates with read-back: all attached. On V11 the name is `Main Tower`, not `Main TS` (that is the name of a hand-added tray section, EXP3 step 3). The `ProductBlock_*` / `FeederBlock_*` entries change as products and feeds are attached (EXP5 step 1).

### 3.3 Feeds

- **Tray feed (default):** `tray.AddFeedStream(name, stage, False)` returns `None`, creates the feed on `stage` and **exports a main-flowsheet material stream with that name**, which is then in `col.AttachedFeeds` and can be connected to an upstream unit (`heater.ProductStream = exported stream`) (EXP4 step 5: feed stages `[('6__Main Tower', 6, ['Fresh Feed'])]`, heater products `['Fresh Feed']`; EXP6 step 2 on the 3-phase template; REPLAY: both column feeds on stage 5). Two tray feeds on different stages work (EXP5 step 2: stages 3 and 7, both in `cf.FeedStreams`). The attachment and the stage persist after save and reopen (EXP4 step 7: `feedstages=[('6__Main Tower', 6, ['Fresh Feed'])]`).
- **Name collision:** if a main-flowsheet stream of that name already exists, the exported stream is named `name_2` (EXP3 step 4: `AddFeedStream('Feed A', 5, False)` gave `col feeds = ['Feed A_2']`). The helper therefore adds the feed under a temporary name, moves the name and the already-made upstream connections of the placeholder onto the exported stream and removes the placeholder (REPLAY `(re-applied to exported stream)` lines; `MaterialStreams.Remove`, EXP4 step 4). The copy of the stream inside the column sub-flowsheet keeps the temporary name unless it is renamed as well (INSPECT: `__s2s_feed_1` in `col_feeds` / `col_matstreams` of both replayed cases while the main-flowsheet stream carries the agent's name).
- **`col.TopLiquidFeed = stream` / `col.BtmVapourFeed = stream`** with an existing main-flowsheet stream: the stream is attached to the column (`AttachedFeeds`) but, on the distillation template, to no stage (`feed stages: []`, EXP4 steps 1 and 2, also after reopen, EXP4 step 7). On the absorber template they are the top liquid and bottom vapour feeds, on the extractor template the two liquid feeds (EXP4 step 6: `feeds: ['Abs Liq In', 'Abs Gas In']`; EXP6 step 3: `feeds: ['LLE Light', 'LLE Heavy']`); both persist after reopen (EXP4 step 7; EXP6 step 4). The helper uses them for `position="top"` / `"bottom"` and for a stream that another column already exported.
- **Negative:** `tray.SpecifyFeedLocation(stream, stage)` fails (E_FAIL) for a main-flowsheet stream in every form tried: the main-flowsheet object, `cf.FeedStreams.Item(name)`, `cf.MaterialStreams.Item(name)` (EXP3 step 4; EXP4 step 1). `cf.FeedStreams.Add(obj)` and `cf.FeedStreams.Add(name)` fail (EXP4 step 2). Moving a `BtmVapourFeed` stream to a stage afterwards fails as well (EXP4 step 2).

### 3.4 Products

- `col.TopVapourProduct`, `col.TopLiquidProduct`, `col.TopWaterProduct`, `col.BtmLiquidProduct = existing main-flowsheet stream`: attached, listed in `col.AttachedProducts` (EXP3 step 4; EXP4 step 3; EXP5 step 1); verified with read-back on all six templates (DISC: all four members on `distillation`, `absorber`, `reboiledabsorber`, `refluxedabsorber`, `threephase` and `liqliqextractor`; EXP6 step 2 on the 3-phase template; EXP6 step 3 on the extractor template; EXP4 step 6 `TopVapourProduct` and `BtmLiquidProduct` on the absorber template). All persist after save and reopen (EXP4 step 7; EXP6 step 4). Each member holds one stream (`get/put`, DISC member list).
- **`TopWaterProduct` makes the condenser three-phase:** the condenser's TypeName changes from `partialcondenser` to `condenser3op` and `CondenserType` from 0 to 3, also after reopen (EXP5 step 1; EXP6 steps 1 and 4). The helper uses it only for the `water_draw` role, i.e. when the sketch draws a water product.
- **Accepted but not attached:** on the empty shell (before `LoadColumnTemplate`) all four members are accepted and `AttachedProducts` stays `[]` (EXP1 step 3; EXP2 step A3). This is why the helper reads every column product back.
- **Negative, condenser-level members:** `Condenser.LiquidStream = stream` and `Reboiler.LiquidStream = stream` are accepted, but the stream appears only in the sub-operation's own `AttachedProducts`, not in `col.AttachedProducts` (EXP5 step 1); `Condenser.VapourStream` raises E_INVALIDARG; `Condenser.VapourProduct` and `Reboiler.LiquidProduct` do not exist (EXP5 step 1). Products are attached on the column object only.

### 3.5 Side draws

- `tray.AddDrawStream(name, phase, stage, True, False)` exports a main-flowsheet product stream, listed in `col.AttachedProducts` (EXP3 step 4; EXP4 step 4). `phase` 1 = liquid draw, 0 = vapour draw, 2 = water draw (EXP4 step 4: `liquid draws ['Kero'] vapour draws ['VapDraw'] water draws ['WaterDraw']`; phase 3 also produced a liquid draw). Name collision gives `name_2` as for feeds (EXP3 step 4: `Kero` -> `Kero_2`); with the main-flowsheet stream removed first, the draw is exported under the wanted name (EXP4 step 4). Draws persist after reopen (EXP4 step 7: seven products including `Kero`, `VapDraw`, `WaterDraw`, `LiqDraw3`). REPLAY cs3 attached `Naphtha`, `Kerosene`, `Diesel`, `AGO` on stages 2, 4, 6, 8 and INSPECT lists all four on the reopened column.
- **Negative:** `tray.SpecifyDrawLocation(existing stream, stage)` fails (EXP4 step 4).

---

## 4. Fired heater (`dynamicfiredheaterop`)

| member | result | source |
|---|---|---|
| `RadInlet.Add(stream)` | **verified** process inlet (radiant tube pass); several inlets accepted, each listed in `RadInlet.Names` and `AttachedFeeds` | EXP3 step 5 (`RadInlet.Names=['Furnace In']`); EXP5 step 3 (second `RadInlet.Add` -> `['In', 'D2']`); REPLAY cs41 (`H-101.RadInlet.Add` three times, `feeds=['Hydrogen', 'Preheated toluene', 'Recycle gas']`); INSPECT (persist) |
| `RadOutlet.Add(stream)` | **verified** process outlet, in `RadOutlet.Names` and `AttachedProducts` | EXP3 step 5; REPLAY cs3, cs41; INSPECT |
| `CombustionProduct = stream` | **verified** flue gas, in `AttachedProducts` | EXP1 step 6; EXP3 step 5 |
| `BurnerFeed = stream` | **verified** fuel, in `AttachedFeeds`; **single-valued**: a second assignment replaces the first (`feeds: ['Fuel']` then `['Air']`) | EXP1 step 6; EXP5 step 3 |
| `FuelsIn.Add(stream)` | **unreliable**: attached once (EXP1 step 6, where it attached the process stream `Furnace In` as a feed), raised E_FAIL once (EXP5 step 3); not used | EXP1 step 6; EXP5 step 3 |
| `ConvInlet.Add` / `ConvOutlet.Add` (object or name) | **negative**, E_FAIL: the default heater has no convection-zone pass | EXP3 step 5 |
| `RadInlet`, `ConvInlet`, `EconInlet`, `RadOutlet`, `ConvOutlet`, `EconOutlet`, `CZTubeFeedStream`, `RZTubeFeedStream` assigned as properties | **negative**: "can not be set" (they are collections / read-only) | EXP1 step 6; EXP2 step B |
| `FeedStream`, `ProductStream`, `Feeds`, `Products`, `TubeFeeds`, `TubeProducts`, `AttachedFeeds.Add`, `AttachedProducts.Add` | **negative**: do not exist | EXP1 step 6; DISC (no single-port member worked) |
| `EnergyStream` | not a member | DISC member list |

A fired heater therefore takes every process inlet through `fired_in` (`RadInlet.Add`), every process outlet through `fired_out` (`RadOutlet.Add`), the flue gas through `flue_gas_out` and one burner stream through `fuel_in`. When the sketch draws both fuel and air, the mixing-points normaliser merges them in a burner mixer (`MIXER_TO_<unit>_BURNER`) so that the single `BurnerFeed` port receives one stream; a second burner-side stream that still reaches the heater directly cannot be attached and is recorded with `skip(..., "single burner feed")`.

---

## 5. Two- and three-phase separators

- `sep3op` (3 Phase Separator): `Feeds.Add`, `VapourProduct`, `LiquidProduct`, `HeavyLiquidProduct`, all attached (EXP3 step 6: products `['S3 Light', 'S3 Vap', 'S3 Heavy']`). `LiquidProduct` is the **light** liquid: with `LiquidProduct = Light` and `HeavyLiquidProduct = Heavy`, `LiquidProduct.name` reads back `Light` and the products are `['Light', 'Heavy']` (EXP5 step 4). This is the `PORTS` mapping `light_liquid_out -> LiquidProduct`, `heavy_liquid_out -> HeavyLiquidProduct`.
- `LightLiquidProduct` exists on neither `sep3op` nor `flashtank` ("can not be set" / unknown name; EXP1 step 7; EXP3 step 6).
- `flashtank` (2-phase Separator) accepts `HeavyLiquidProduct` silently and attaches nothing (section 2.4). None of the 3-phase spellings listed in section 6 creates a 3-phase vessel; before `sep3op` was found, the template's fallback produced a 2-phase `flashtank` for the cs2 settler (DISC row `separator_3phase`: "template first choice FAILED", type `flashtank`; INSPECT cs2).

---

## 6. Negative results on V11

Type strings that do **not** exist (`Operations.Add` raises E_FAIL):

- 3-phase separator spellings: `separator3op`, `3phaseseparatorop`, `threephaseseparatorop`, `3phaseseparator`, `separator3`, `3phasesep`, `sep3phase`, `threephasesep`, `separator3phase`, `vesselop`, `3-phase separator` (EXP1 step 7; DISC `separator_3phase` tried list); `3phaseflashtank`, `flashtank3`, `flashtank3phase`, `threephaseflashtank`, `3phasetank`, `separatorthreephase`, `3phase`, `separator3phaseop`, `3phasesepop`, `3phaseflash`, `3-phaseseparator`, `three-phaseseparator`, `3 phase separator`, `threephaseseparator`, `3phaseseperator`, `sep3` (EXP2 step C). Only `sep3op` works.
- Reactors: `cstrop`, `pfrop`, `yieldshiftreactorop`, `cstr`, `pfr`, `gibbs`, `equilibrium` (EXP1 step 7); `cstreactorop`, `cstrreactorop`, `plugflowreactorop`, `tubularreactorop`, `yieldreactorop`, `yieldshiftop` (EXP2 step C); DISC also tried `cstr`, `cstreactorop`, `yieldreactorop`. No yield-shift reactor string was found.
- Columns: `threephasedistillation` (EXP1 step 5), `3phasedistillation`, `threephasedist` (EXP2 step C), `liquidliquidextractor`, `extractor`, `lleop` (DISC); `reboiledabsorberop`, `refluxedabsorberop` were in the old candidate lists and are superseded by the verified `reboiledabsorber` / `refluxedabsorber` (DISC first choices). `ColumnOp` creates an empty `columnop` sub-flowsheet (section 1); `shdistop` has no `ColumnFlowsheet` (EXP1 step 5).
- Others: `componentsplitterop` (the component splitter is `fractop`), `ejectorop`, `ejector`, `ejectop`, `tank` (EXP1 step 7; EXP2 step C; DISC), `separator`, `mixer`, `tee`, `cooler`, `heater`, `pump`, `valve`, `compressorop`, `firedheaterop`, `heatexchangerop` (EXP2 step C). `separatorop` is not a V11 type either (the only 2-phase vessel strings that work are `flashtank` and `tankop`). `CompressOp` and `HeatExchanger` were never tried and were removed from the template as unneeded.
- Sub-flowsheet type strings: `traysectionop`, `tray section`, `condenser`, `condenserop`, `reboiler`, `reboilerop` fail; `traysection` / `TraySection` work (EXP3 step 3).

Members that do not exist or do not attach:

- `LightLiquidProduct` (vessels), `AttachedFeeds.Add`, `AttachedProducts.Add` (every unit), the fired-heater members listed in section 4, `Condenser.VapourProduct`, `Reboiler.LiquidProduct`, `Condenser.VapourStream` (raises), `SpecifyFeedLocation`, `SpecifyDrawLocation`, `ColumnFlowsheet.FeedStreams.Add`.
- Accepted without attaching: `HeavyLiquidProduct` on `flashtank` and `tankop` (2.4, DISC); the four column product members on a column without a loaded template (3.4); `Condenser.LiquidStream` / `Reboiler.LiquidStream` attach inside the sub-flowsheet only (3.4).

---

## 7. Lifecycle, saving, streams and basis

- **Start-up.** `Dispatch("HYSYS.Application")` and `DispatchEx("HYSYS.Application")` both start a **new** `AspenHysys.exe` (new PID in `tasklist`; 17.8 s for `Dispatch`, EXP5 step 0; `PIDs after DispatchEx … new: [29772]`, EXP1 first lines). Neither attaches to a running instance. `SimulationCases.Add` right after the call worked (EXP5 step 0, "immediate SimulationCases.Add OK").
- **Start-up / shutdown race.** In the pipeline, `hysys.SimulationCases.Add(case_name)` raised `com_error -2147023170` (RPC call failed) on attempt 2 of every run and on cs3 attempt 4 (LOGS `logs/cs41/executor_run_20261022_052103.log`, `logs/cs4/executor_run_20261022_051241.log`, `logs/cs31/executor_run_20261022_074224.log`, `logs/cs3/executor_run_20261022_070520.log` and `_070743.log`); each of those attempts started a new instance while the previous attempt's instance was still alive. The failures stopped in every run once a sleep-and-retry loop around `Add` was in place. Reproduced during the investigation of the change plan (not in the saved step logs): creating a case while the previous instance is still exiting fails with E_FAIL, and starting two instances at the same moment fails with a server fault. Waiting until the previous process has left `tasklist`, then creating the instance and the case, works on the first try (EXP5 step 0: `after Quit: waited 14s; extra PIDs still alive: []` … `SimulationCases.Add OK on try 1`). The template's Step 1 therefore uses `DispatchEx`, prints the PID it created and retries `SimulationCases.Add` with 5 s sleeps; the executor waits for the previous attempt's instance before starting the next one.
- **Quit.** `hysys.Quit()` returns immediately (0.0 s) and the process exits within about 15 s, also for a visible instance with an unsaved, modified case; no dialog appears (QUIT: `Quit() returned after 0.0s`, `still alive after Quit: []`; EXP5 step 0; EXP6 step 4 `instance 1 gone: True`). Instances that no script quits never exit on their own (EXP1: seven `AspenHysys.exe` left by the morning's pipeline runs were still alive at 09:25).
- **Save.** `case.SaveAs(path)` works with the solver held (`case.Solver.CanSolve = False`), after releasing it (`CanSolve = True`, `Solver.IsSolving` False), and a second time to the same path; `case.Save()` works (EXP1 step 8). Saved cases reopen with every attachment through `hysys.SimulationCases.Open(path)` (EXP4 step 7; EXP6 step 4; INSPECT). `SaveAs(path, True)` is a `TypeError` ("takes from 1 to 2 positional arguments but 3 were given", LOGS cs31). In late cs3/cs31 attempts `SaveAs(path)` raised E_INVALIDARG (`logs/cs3/executor_run_20261022_072014.log`; `logs/cs31/executor_run_20261022_081400.log` … `_082828.log`) after fixer-edited helper code, with older instances holding the same path; this was not reproduced in isolation (EXP1 step 8 saved with the leftover instances alive). The template saves to a per-attempt path and retries once with the solver held.
- **Solver.** `case.Solver.CanSolve = False` holds the solver while the flowsheet is built and `True` releases it; `case.Solver.IsSolving` is readable (EXP1 step 8; REPLAY `[solver]` lines). Column templates load and streams attach in both states (EXP3 steps 1 and 2).
- **Streams.** `flowsheet.MaterialStreams.Add(name)` returns the stream; `MaterialStreams.Item(name)`, `MaterialStreams.Remove(name)` (EXP4 step 4), `MaterialStreams.Names`; renaming through `stream.name = ...` (REPLAY / INSPECT: the streams exported by the column carry the agents' names after the helper's rename); `flowsheet.EnergyStreams.Add(name)` (DISC energy probes). `AttachedFeeds.Names`, `AttachedProducts.Names`, `UpstreamOpers`, `DownstreamOpers` are the read-back truth (INSPECT).
- **Basis.** `case.BasisManager.StartBasisChange()` / `EndBasisChange()`, `FluidPackages.Add()`, `fluidpkg.PropertyPackageName = "PengRob"` reads back `'Peng-Robinson'` (DISC `property_package`; REPLAY line 1), `fluidpkg.Components.Add("Benzene")`, `("Toluene")`, `("Hydrogen")`, `("Methane")`, `("H2O")`, `("Ethane")` (DISC; EXP scripts). `case.Visible = True` and `hysys.Visible` are settable (QUIT; template Step 1).

---

## 8. PFD (the flowsheet diagram): graphics only

Source: **PROBE** = `pfd_probe.py`, `pfd_probe2.py`, `pfd_probe3.py` in `%LOCALAPPDATA%\Temp\s2s_exp` (reports `pfd_probe\report*.json`), run on 2026-10-24 on the same HYSYS V11 build. Used by `sketch2sim/pfd_layout.py`, the template's `layout_pfd()` (Step 7) and `tools/hysys_layout_pfd.py`.

- **A PFD is a view.** `case.Flowsheet.PFDs.Count` is 0 in a case built without showing it (the pipeline before this change). After `case.Visible = True`, `case.Activate()` and a few `hysys.DoEvents()` calls, `PFDs.Count` is 1 (`Names` = `['PFD 1']`) and `PFDs.ActivePFD` returns it; every object created from then on gets its graphic item at once, placed by HYSYS in a row in creation order. A case that was never shown gets its items (crowded) when the `.hsc` is first opened in the GUI (PROBE 1 and 2).
- **Items.** `pfd.Items` returns nothing; `pfd.GetItems(itemType, visibility)` works: `-2` (`pfdOperation`) returns the named items of the unit operations AND the material streams (6 units + 10 streams = 16 names), `-3` the stream lines (unnamed), `-4` the labels (unnamed, by index), `-1` everything; visibility `2` = visible and hidden. `Item(name)` by name (PROBE 1).
- **Item members.** `XPosition` / `YPosition` get/put (PFD units, X to the right, **Y up**: items placed at a smaller y appear lower on the screen, as the laid-out cs4 case showed; `YPosition` is the lower-left corner, consistent with HYSYS centring a 12-unit stream icon at y = -185 on a 56-unit pump at y = -211), `Width` / `Height` (get; put ignored), `Rotation` (`RotateBy(1)` reads back 1), `Hidden` get/put, `IconName`, `Object` (the flowsheet object), `Type`, `Nozzles`; `AutoPosition()` only nudges an item; `pfd.MoveBy([item], dx, dy)` works; `pfd.Centre()` centres the view; `pfd.Extent` is `(xmin, ymin, xmax, ymax)` (PROBE 1 and 2).
- **Sizes** (PFD units): material stream icon 34 x 12; `pumpop` 56 x 56; `heaterop` 64 x 56; `compressor` 72 x 56; `mixerop` 48 x 76; `flashtank` 56 x 144; `distillation` 138 x 228. Labels are about 15 units per character wide (`Feed` 61, `CompOut` 115), which is why long names dominate the picture (PROBE 1).
- **The one rule.** HYSYS silently refuses a move that would put a unit left of one of its feed stream icons or right of one of its product icons, or a stream icon across its unit; the same move succeeds once the neighbours have moved, so a layout that respects feed < unit < product converges in two or three passes (PROBE 2: 13 of 16 targets refused in one batch, 3 after a second pass; PROBE 3: all 16 exact after the products were moved first). A recycle icon (product of a downstream unit, feed of an upstream one) can satisfy neither side.
- **Lines.** `PFDConnection.BendPoints` is readable (a tuple of `(x, y)` pairs); setting it raises E_NOTIMPL. HYSYS re-routes every line and re-orients the stream icons itself when items move, so a layout places icons and leaves the lines to HYSYS (PROBE 1 and 2).
- **Labels.** Separate items (`GetItems(-4, 2)`) that follow their owner when it moves; `Hidden = True` persists after `SaveAs` and reopen; they have no owner property (the owner is found by nudging it). No font or size members: the PFD font is a HYSYS preference (`Application.LoadPreferences(file)` exists) (PROBE 1 and 2).
- **Persistence.** Positions read back exactly after `SaveAs` and `SimulationCases.Open` (PROBE 2 and 3); `case.IsDirty` turns True after PFD edits and False after the save; nothing of the simulation changes (section 7 attachments unchanged, read-back after the layout in Step 7).
- **Energy streams.** A unit's energy stream is listed in its `AttachedFeeds` and `AttachedProducts` (a reactor lists `E-Reactor` in both, cs4 read-back `logs/cs4_v11/step9_topology.json`); the layout code drops the names of `flowsheet.EnergyStreams` from the attachment graph and hangs the duty icons below their unit. Energy streams have PFD items too (`GetItems(-2, 2)` lists them, and they can be moved: the cs4 astra layout moved `E-Reactor`).
- **Also verified:** `Application.DoEvents()`, `SimulationCase.Activate()`, `SimulationCases.Open(path)`, `case.Close(False)`; `Application.PlayScript(file)` and `LoadPreferences(file)` exist (type library) and were not exercised.
