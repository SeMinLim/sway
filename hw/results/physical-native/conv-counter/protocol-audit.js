function protocolAudit(){
const checks=[];
for(const lanes of [1,2]){
 const groups=40/lanes, initial=[0,0,0,false,false,0,0];
 const queue=[initial],seen=new Set([JSON.stringify(initial)]);
 let transitions=0,issues=0;
 const assert=(condition)=>{if(!condition)throw Error("Protocol invariant");};
 for(let h=0;h<queue.length;h++){
  const state=queue[h]; const [done,oldCount,newCount,active,issue,issued,collected]=state;
  assert(0<=collected&&collected<=issued&&issued<=groups);
  assert(newCount>=0&&newCount<groups&&oldCount>=0&&oldCount<=groups);
  if(active&&issue)assert(oldCount===newCount&&newCount===issued&&issued<groups);
  if(!issue)assert(newCount===0);
  if(!active)assert(newCount===0&&!issue);
  const next=[state,initial]; // Arbitrary stall; cold/warm hardware reset.
  if(!active&&done<2)next.push([done,0,newCount,true,true,0,0]);
  if(active&&issue){
   assert(oldCount===newCount);
   for(let lane=0;lane<lanes;lane++)assert(oldCount*lanes+lane===issued*lanes+lane);
   const last=newCount===groups-1;
   next.push([done,oldCount+1,last?0:newCount+1,true,!last,issued+1,collected]);issues++;
  }
  if(active&&collected<issued){
   const last=collected===groups-1;
   if(last)assert(issued===groups&&!issue&&newCount===0);
   next.push([done+(last?1:0),oldCount,newCount,!last,issue,issued,collected+1]);
  }
  transitions+=next.length;
  for(const n of next){const key=JSON.stringify(n);if(!seen.has(key)){seen.add(key);queue.push(n);}}
 }
 assert(queue.some(s=>s[0]===2&&!s[3]));
 checks.push({lanes,groups,consecutiveTokens:2,reachableStates:seen.size,transitions,acceptedIssueTransitions:issues,resetStatesChecked:seen.size,status:"pass"});
}
return checks;
}
console.log(JSON.stringify(protocolAudit(), null, 2));
