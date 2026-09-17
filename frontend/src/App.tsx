import { useState } from "react";
import type { ViewName } from "./types";




export default function App() {

  const [activeView , setActiveView] = useState<ViewName>("roadmap");


  function handleViewChange(view:ViewName){
    setActiveView(view)
  }


  return (
    <main>
      <h1>Welcome Wave</h1>
      <nav>
        <button onClick={()=>handleViewChange("roadmap")}>Roadmap</button>
        <button onClick={()=>handleViewChange("ask")}>Ask</button>
        <button onClick={()=>handleViewChange("verifyQueue")}>Verify Queue</button>
        <button onClick={()=>handleViewChange("dashboard")}>Dashboard</button>
      </nav>

      <section>
        {activeView=="roadmap"&&<p>Roadmap view</p>}
        {activeView=="ask"&&<p>Ask view</p>}
        {activeView=="verifyQueue"&&<p>Verify Queue view</p>}
        {activeView=="dashboard"&&<p>Dashboard view</p>}
      </section>
    </main>
  )




  return <main>Welcome Wave Onboarding Console</main>;
}
