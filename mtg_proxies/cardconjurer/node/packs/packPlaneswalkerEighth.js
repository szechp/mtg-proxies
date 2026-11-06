// Ours, not Card Conjurer's: an 8th-Edition single-face planeswalker, built the way CC builds
// packPlaneswalkerSeventh.js -- the regular frame art plus versionPlaneswalker.js for the loyalty
// costs. Frames, masks, title / mana / type regions and art bounds are pack8th.js's; the abilities
// fill pack8th's rules box (measured off img/frames/8th/rules.png: x 171-1838, y 1751-2521 of
// 2010x2814), leaving room on the left for the cost badges. harness.js adds the loyalty badge,
// the even ability sizing (equalizePlaneswalkerAbilities) and hides CC's modern ability stripes.
var masks = [{src:'/img/frames/8th/pinline.png', name:'Pinline'}, {src:'/img/frames/8th/title.png', name:'Title'}, {src:'/img/frames/8th/type.png', name:'Type'}, {src:'/img/frames/8th/rules.png', name:'Rules'}, {src:'/img/frames/8th/frame.png', name:'Frame'}, {src:'/img/frames/8th/border.png', name:'Border'}];
var masks2 = [{src:'/img/frames/8th/border.png', name:'Border'}];
availableFrames = [
	{name:'White Frame', src:'/img/frames/8th/w.png', masks:masks},
	{name:'Blue Frame', src:'/img/frames/8th/u.png', masks:masks},
	{name:'Black Frame', src:'/img/frames/8th/b.png', masks:masks},
	{name:'Red Frame', src:'/img/frames/8th/r.png', masks:masks},
	{name:'Green Frame', src:'/img/frames/8th/g.png', masks:masks},
	{name:'Multicolored Frame', src:'/img/frames/8th/m.png', masks:masks},
	{name:'Artifact Frame', src:'/img/frames/8th/a.png', masks:masks},
	{name:'Colorless Frame', src:'/img/frames/8th/c.png', masks:masks},
	{name:'Land Frame', src:'/img/frames/8th/l.png', masks:masks},
	{name:'White Border', src:'/img/frames/white.png', masks:masks2, noDefaultMask:true},
	{name:'Silver Border', src:'/img/frames/silver.png', masks:masks2, noDefaultMask:true},
	{name:'Gold Border', src:'/img/frames/gold.png', masks:masks2, noDefaultMask:true}
];
document.querySelector('#loadFrameVersion').disabled = false;
document.querySelector('#loadFrameVersion').onclick = async function() {
	await resetCardIrregularities();
	card.version = 'planeswalkerEighth';
	card.showsFlavorBar = false;
	card.onload = '/js/frames/versionPlaneswalker.js';
	loadScript('/js/frames/versionPlaneswalker.js');
	card.artBounds = {x:180/2010, y:341/2814, width:1656/2010, height:1216/2814};
	autoFitArt();
	card.setSymbolBounds = {x:0.9079, y:0.5886, width:0.12, height:0.0391, vertical:'center', horizontal: 'right'};
	resetSetSymbol();
	card.watermarkBounds = {x:0.5, y:0.7605, width:0.75, height:0.2305};
	resetWatermark();
	loadTextOptions({
		mana: {name:'Mana Cost', text:'', y:202/2814, width:0.9147, height:65/2100, oneLine:true, size:65/1638, align:'right', shadowX:-0.001, shadowY:0.0029, manaCost:true, manaSpacing:0},
		title: {name:'Title', text:'', x:0.09, y:0.0629, width:0.824, height:0.0429, oneLine:true, font:'matrixb', size:0.0429},
		type: {name:'Type', text:'', x:205/2010, y:1611/2814, width:1601/2010, height:0.0358, oneLine:true, font:'matrixb', size:0.0358},
		ability0: {name:'Ability 1', text:'', x:362/2010, y:1751/2814, width:1446/2010, height:0.0915, size:0.0358},
		ability1: {name:'Ability 2', text:'', x:362/2010, y:0, width:1446/2010, height:0.0915, size:0.0358},
		ability2: {name:'Ability 3', text:'', x:362/2010, y:0, width:1446/2010, height:0.0915, size:0.0358},
		ability3: {name:'Ability 4', text:'', x:362/2010, y:0, width:1446/2010, height:0, size:0.0358},
		loyalty: {name:'Loyalty', text:'', x:0.8072, y:0.8916, width:0.1367, height:0.0429, size:0.0429, font:'matrixbsc', oneLine:true, align:'center', color:'white'}
	});
}
loadFramePack();
