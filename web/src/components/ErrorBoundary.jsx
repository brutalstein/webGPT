import { Component } from 'react';

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error('OS web arayüzü render hatası', error, info);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <main className="error-boundary" role="alert">
        <div className="error-boundary-card">
          <span className="eyebrow">Arayüz güvenli moda geçti</span>
          <h1>Web arayüzü oluşturulamadı.</h1>
          <p>{this.state.error?.message || 'Bilinmeyen React hatası'}</p>
          <button type="button" className="primary-button compact" onClick={() => window.location.reload()}>
            Arayüzü yeniden yükle
          </button>
        </div>
      </main>
    );
  }
}
