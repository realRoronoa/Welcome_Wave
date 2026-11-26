/* The app's chunk: sign-in and the workspace. main.tsx loads it lazily, so the public site never downloads it (until a visitor
   points at "Log in", when src/site/handover.ts prefetches it). The theme module applies the saved light/dark choice on import,
   before the app paints; the global stylesheets come next, so page and component stylesheets imported below refine them. */
import "./theme";
import "./styles.css";
import "./styles/shell.css";
import App from "./App";

export default App;
