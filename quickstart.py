#!/usr/bin/env python
"""
Quick Start Script for Algorithmic Trading Dashboard
Runs minimal setup and launches the Streamlit dashboard immediately.

Usage:
    python quickstart.py
"""

import os
import sys
import logging
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def check_dependencies():
    """Verify all required packages are installed."""
    logger.info("🔍 Checking dependencies...")
    
    required_packages = [
        'pandas',
        'numpy',
        'streamlit',
        'plotly',
        'sqlalchemy',
        'yfinance'
    ]
    
    missing = []
    for package in required_packages:
        try:
            __import__(package)
            logger.info(f"  ✅ {package}")
        except ImportError:
            missing.append(package)
            logger.warning(f"  ❌ {package}")
    
    if missing:
        logger.error(f"\n❌ Missing packages: {', '.join(missing)}")
        logger.info(f"\n📦 Install with:")
        logger.info(f"   pip install {' '.join(missing)}")
        return False
    
    logger.info("✅ All dependencies installed!\n")
    return True


def check_project_structure():
    """Verify project structure is intact."""
    logger.info("📁 Checking project structure...")
    
    required_files = [
        'src/__init__.py',
        'src/backtest_engine.py',
        'src/risk_manager.py',
        'src/strategies.py',
        'src/market_screener.py',
        'src/metrics.py',
        'dashboard.py',
        'main.py',
    ]
    
    missing = []
    for file in required_files:
        if os.path.exists(file):
            logger.info(f"  ✅ {file}")
        else:
            missing.append(file)
            logger.warning(f"  ❌ {file}")
    
    if missing:
        logger.error(f"\n❌ Missing files: {', '.join(missing)}")
        return False
    
    logger.info("✅ Project structure intact!\n")
    return True


def create_data_directory():
    """Ensure data directory exists."""
    logger.info("📂 Setting up data directory...")
    
    Path('data').mkdir(exist_ok=True)
    logger.info("  ✅ data/ directory ready\n")


def print_welcome():
    """Print welcome banner."""
    banner = """
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║   🤖 ALGORITHMIC TRADING DASHBOARD                        ║
    ║                                                           ║
    ║   Phase 6 & 7: Risk Management & Interactive UI          ║
    ║                                                           ║
    ║   Version: 1.0.0 (Production-Ready)                      ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝
    
    📊 Features:
       • Market Screening with quantitative filters
       • SMA Crossover & RSI Mean Reversion strategies
       • Risk-managed backtesting with position sizing
       • Interactive performance curves
       • Real-time risk management calculator
    
    🚀 Quick Commands:
       • streamlit run dashboard.py        (Launch dashboard)
       • python main.py                    (Headless pipeline)
       • python quickstart.py              (This script)
    
    ⚙️  Configuration:
       • Modify sidebar sliders in dashboard
       • Or edit config dict in main.py
       • Database: docker-compose.yml
    
    📚 Documentation:
       • README.md                         (Full guide)
       • src/risk_manager.py               (Risk API)
       • src/backtest_engine.py            (Backtest API)
    
    ⚠️  Disclaimer:
       This is for educational purposes. Test thoroughly
       before any live trading. Past performance ≠ future results.
    
    """
    print(banner)


def main():
    """Main quickstart function."""
    os.chdir(Path(__file__).parent)
    
    print_welcome()
    
    if not check_dependencies():
        sys.exit(1)
    
    if not check_project_structure():
        sys.exit(1)
    
    create_data_directory()
    
    logger.info("=" * 60)
    logger.info("🎉 Setup complete! Ready to launch dashboard.\n")
    logger.info("Starting Streamlit app...\n")
    logger.info("=" * 60)
    
    # Launch Streamlit
    os.system("streamlit run dashboard.py")


if __name__ == "__main__":
    main()
