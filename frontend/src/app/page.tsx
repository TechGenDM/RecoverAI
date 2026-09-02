import styles from "./page.module.css";

export default function Home() {
  return (
    <div className={styles.page}>
      <main className={styles.main}>
        <h1>RecoverAI</h1>
        <h2>AI Revenue Recovery</h2>
        <p>This is the foundation for the RecoverAI dashboard.</p>
      </main>
    </div>
  );
}
