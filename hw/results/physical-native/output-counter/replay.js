function step(s,a,resetCounter){let {on,cnt}=s,out=null;if(a==="reset"){on=false;if(resetCounter)cnt=0;}else if(a==="load"&&!on){on=true;cnt=0;}else if(a==="accept"&&on){out=cnt;if(cnt===56)on=false;else cnt++;}return{on,cnt,out};}
let states=0,transitions=0;
for(const on of [false,true]) for(let a=0;a<(on?57:64);a++) for(let b=0;b<(on?57:64);b++){
    if(on&&a!==b) continue;
    states++;
    for(const op of ["reset","load","accept","stall"]){
        const x=step({on,cnt:a},op,true),y=step({on,cnt:b},op,false);
        if(x.on!==y.on||x.out!==y.out||(x.on&&x.cnt!==y.cnt)) throw Error("Counter relation failed");
        transitions++;
    }
}
console.log(JSON.stringify({status:"pass",states,transitions}));
