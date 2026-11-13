/**
 * Showdown 챔피언스 데이터를 JSON으로 내보내는 스크립트.
 *
 * Showdown이 실제 배틀에서 쓰는 데이터(champions 모드 적용 후)를 그대로 꺼내므로,
 * 파이썬 쪽 계산(champions_calc.py)과 서버의 계산이 같은 숫자를 쓰게 된다.
 *
 * 사용법 (project 폴더에서, 서버를 한 번 이상 실행해서 dist 폴더가 있어야 함):
 *   node tools/export_champions_data.js
 *   node tools/export_champions_data.js <showdown 폴더> <출력 폴더> <포맷 ID>
 */
'use strict';
const fs = require('fs');
const path = require('path');

const DEFAULT_FORMAT = 'gen9championsbssregmc';

function pick(obj, keys) {
	const out = {};
	for (const k of keys) {
		if (obj[k] !== undefined && obj[k] !== null && obj[k] !== false) out[k] = obj[k];
	}
	return out;
}

function exportData(Dex, outDir, formatId = DEFAULT_FORMAT) {
	const format = Dex.formats.get(formatId);
	if (!format.exists) throw new Error(`포맷을 찾을 수 없음: ${formatId}`);
	const dex = Dex.mod(format.mod);
	const ruleTable = Dex.formats.getRuleTable(format);

	// ---------- 포켓몬 ----------
	const species = {};
	for (const s of dex.species.all()) {
		if (s.isNonstandard || s.tier === 'Illegal' || s.isCosmeticForme) continue;
		species[s.id] = {
			name: s.name,
			num: s.num,
			types: s.types,
			baseStats: s.baseStats,
			abilities: Object.values(s.abilities),
			weightkg: s.weightkg,
			baseSpecies: s.baseSpecies,
			forme: s.forme || '',
			isMega: !!s.isMega,
			requiredItem: s.requiredItem || null,
			otherFormes: s.otherFormes || [],
			tier: s.tier,
			legal: !ruleTable.isBannedSpecies(s),
		};
	}

	// ---------- 기술 ----------
	const moves = {};
	for (const m of dex.moves.all()) {
		if (m.isNonstandard || m.isZ || m.isMax) continue;
		moves[m.id] = {
			name: m.name,
			type: m.type,
			category: m.category,
			basePower: m.basePower,
			accuracy: m.accuracy === true ? null : m.accuracy,
			priority: m.priority,
			pp: m.pp,
			target: m.target,
			flags: Object.keys(m.flags || {}).filter(f => m.flags[f]),
			hasBasePowerCallback: !!m.basePowerCallback,
			hasDamageCallback: !!m.damageCallback,
			...pick(m, [
				'critRatio', 'willCrit', 'multihit', 'drain', 'recoil', 'heal', 'status', 'boosts',
				'volatileStatus', 'sideCondition', 'weather', 'terrain', 'pseudoWeather',
				'forceSwitch', 'selfSwitch', 'selfdestruct', 'ohko', 'damage', 'breaksProtect',
				'ignoreDefensive', 'ignoreEvasion', 'ignoreAbility',
				'overrideOffensivePokemon', 'overrideOffensiveStat', 'overrideDefensiveStat',
			]),
			secondaries: (m.secondaries || []).map(sec => pick(sec, ['chance', 'status', 'volatileStatus', 'boosts'])),
			selfBoost: m.self && m.self.boosts ? m.self.boosts : (m.selfBoost && m.selfBoost.boosts) || null,
			shortDesc: m.shortDesc,
		};
	}

	// ---------- 도구 ----------
	const items = {};
	for (const it of dex.items.all()) {
		if (it.isNonstandard) continue;
		items[it.id] = {
			name: it.name,
			megaStone: it.megaStone || null,
			shortDesc: it.shortDesc,
		};
	}

	// ---------- 특성 ----------
	const abilities = {};
	for (const a of dex.abilities.all()) {
		if (a.isNonstandard || !a.exists) continue;
		abilities[a.id] = { name: a.name, shortDesc: a.shortDesc };
	}

	// ---------- 타입 상성: typechart[공격 타입][방어 타입] = 배율 ----------
	// Showdown damageTaken 코드: 0 보통, 1 효과 굉장함, 2 효과 별로, 3 효과 없음
	const codeToMult = { 0: 1, 1: 2, 2: 0.5, 3: 0 };
	const types = dex.types.all().filter(t => !t.isNonstandard).map(t => t.name);
	const typechart = {};
	for (const atk of types) {
		typechart[atk] = {};
		for (const def of types) {
			typechart[atk][def] = codeToMult[dex.types.get(def).damageTaken[atk]];
		}
	}

	// ---------- 성격 ----------
	const natures = {};
	for (const n of dex.natures.all()) natures[n.id] = { name: n.name, plus: n.plus || null, minus: n.minus || null };

	// ---------- 배우는 기술 (규칙상 쓸 수 있는 포켓몬만) ----------
	const learnsets = {};
	for (const [id, s] of Object.entries(species)) {
		if (!s.legal) continue;
		learnsets[id] = [...dex.species.getMovePool(id)].filter(mid => moves[mid]).sort();
	}

	const meta = {
		format: format.name,
		formatId: format.id,
		mod: format.mod,
		exportedAt: new Date().toISOString(),
		counts: {
			species: Object.keys(species).length,
			legalSpecies: Object.values(species).filter(s => s.legal).length,
			moves: Object.keys(moves).length,
			items: Object.keys(items).length,
			abilities: Object.keys(abilities).length,
		},
		notes: [
			"species.legal: 해당 포맷 규칙(Flat Rules 등)에서 사용할 수 있는지",
			"typechart[공격 타입][방어 타입] = 배율",
			"챔피언스 능력치: HP = 종족값 + 포인트 + 75, 그 외 = (종족값 + 포인트 + 20) x 성격 보정 (레벨 50)",
		],
	};

	fs.mkdirSync(outDir, { recursive: true });
	const files = { species, moves, items, abilities, typechart, natures, learnsets, meta };
	for (const [name, data] of Object.entries(files)) {
		fs.writeFileSync(path.join(outDir, `${name}.json`), JSON.stringify(data, null, 1));
	}
	return meta;
}

module.exports = { exportData };

if (require.main === module) {
	const psDir = path.resolve(process.argv[2] || 'showdown/pokemon-showdown');
	const outDir = path.resolve(process.argv[3] || 'data/processed/champions');
	const formatId = process.argv[4] || DEFAULT_FORMAT;
	const distSim = path.join(psDir, 'dist', 'sim');
	if (!fs.existsSync(distSim)) {
		console.error(`Showdown 빌드 폴더가 없음: ${distSim}\nShowdown 폴더에서 'node build' 또는 서버를 한 번 실행한 뒤 다시 시도하세요.`);
		process.exit(1);
	}
	const { Dex } = require(distSim);
	const meta = exportData(Dex, outDir, formatId);
	console.log(`[${meta.format}] 내보내기 완료 → ${outDir}`);
	console.log(meta.counts);
}
