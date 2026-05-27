"""
Interactive Streamlit Dashboard for Algorithmic Trading System.

This dashboard provides executive-level visualization and control of:
- Market screening results with qualification filters
- Strategy performance metrics and comparisons
- Dynamic portfolio risk management
- Interactive performance curves and analysis

Author: Quantitative Development Team
Version: 1.0.0
"""

import streamlit as st
import pandas as pd
import numpy as np
import logging
from datetime import date, timedelta
from typing import List, Dict, Tuple
import plotly.graph_objects as go
import plotly.express as px

from src.data_loader import fetch_historical_data, save_data_to_csv
from src.db_pipeline import init_database, load_csv_to_sql
from src.backtest_engine import BacktestEngine, StrategyComparator
from src.strategies import SMACrossoverStrategy, RSIMeanReversionStrategy
from src.metrics import MetricsCalculator
from src.market_screener import MarketScreener
from src.optimizer import StrategyOptimizer
from src.risk_manager import RiskManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database Configuration
DB_USER = "quant_user"
DB_PASSWORD = "quant_password123"
DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "quant_research"
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


def init_session_state():
    """Initialize Streamlit session state variables."""
    if 'screener_results' not in st.session_state:
        st.session_state.screener_results = None
    if 'backtest_results' not in st.session_state:
        st.session_state.backtest_results = {}
    if 'market_data' not in st.session_state:
        st.session_state.market_data = {}


def render_header():
    """Render dashboard header and title."""
    st.set_page_config(
        page_title="Algo Trading Dashboard",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    st.markdown("""
    <style>
        .main {
            padding-top: 0rem;
        }
        .metric-card {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            border-radius: 10px;
            color: white;
            margin: 10px 0;
        }
        .qualified-badge {
            background: #00d084;
            color: white;
            padding: 8px 12px;
            border-radius: 20px;
            font-weight: bold;
            margin: 5px;
            display: inline-block;
        }
        .disqualified-badge {
            background: #ff4444;
            color: white;
            padding: 8px 12px;
            border-radius: 20px;
            font-weight: bold;
            margin: 5px;
            display: inline-block;
        }
    </style>
    """, unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.title("🤖 Algorithmic Trading Dashboard")
        st.markdown("**Quantitative Research & Portfolio Risk Management System**")


def render_sidebar_controls() -> Dict:
    """Render sidebar controls and return configuration parameters."""
    with st.sidebar:
        st.header("⚙️ Configuration")
        
        # Capital and Risk Controls
        st.subheader("Capital & Risk Management")
        initial_capital = st.slider(
            "Initial Capital ($)",
            min_value=1000,
            max_value=100000,
            value=10000,
            step=1000,
            help="Starting portfolio capital for backtests"
        )
        
        max_risk_per_trade = st.slider(
            "Max Risk Per Trade (%)",
            min_value=0.5,
            max_value=5.0,
            value=1.0,
            step=0.5,
            help="Maximum capital risked on any single trade"
        )
        
        # Strategy Parameters
        st.subheader("Strategy Parameters")
        
        col1, col2 = st.columns(2)
        with col1:
            sma_short = st.number_input(
                "SMA Fast Period",
                min_value=5,
                max_value=50,
                value=10,
                help="Short-term moving average window"
            )
        with col2:
            sma_long = st.number_input(
                "SMA Slow Period",
                min_value=20,
                max_value=200,
                value=30,
                help="Long-term moving average window"
            )
        
        col1, col2 = st.columns(2)
        with col1:
            rsi_period = st.number_input(
                "RSI Period",
                min_value=7,
                max_value=28,
                value=14,
                help="RSI calculation window"
            )
        with col2:
            rsi_oversold = st.number_input(
                "RSI Oversold",
                min_value=10,
                max_value=40,
                value=30,
                help="RSI oversold threshold"
            )
        
        # Risk Management Controls
        st.subheader("Risk Management")
        
        col1, col2 = st.columns(2)
        with col1:
            stop_loss_pct = st.slider(
                "Stop Loss (%)",
                min_value=1.0,
                max_value=20.0,
                value=5.0,
                step=0.5,
                help="Stop loss percentage below entry"
            )
        with col2:
            take_profit_pct = st.slider(
                "Take Profit (%)",
                min_value=5.0,
                max_value=100.0,
                value=20.0,
                step=5.0,
                help="Take profit percentage above entry"
            )
        
        # Date Range
        st.subheader("Data Range")
        end_date = st.date_input("End Date", value=date.today())
        days_back = st.slider(
            "Days Back",
            min_value=30,
            max_value=730,
            value=365,
            step=30,
            help="Historical data lookback period"
        )
        start_date = end_date - timedelta(days=days_back)
        
        st.markdown("---")
        
        return {
            'initial_capital': initial_capital,
            'max_risk_per_trade': max_risk_per_trade,
            'sma_short': sma_short,
            'sma_long': sma_long,
            'rsi_period': rsi_period,
            'rsi_oversold': rsi_oversold,
            'stop_loss_pct': stop_loss_pct,
            'take_profit_pct': take_profit_pct,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat()
        }


def render_screener_section(config: Dict):
    """Render market screener results section."""
    st.header("🎯 Market Screener Results")
    
    if st.button("🔄 Run Market Screener", key="screener_button"):
        with st.spinner("🔍 Screening market..."):
            try:
                screener = MarketScreener()
                qualified, disqualified = screener.screen()
                st.session_state.screener_results = (qualified, disqualified)
            except Exception as e:
                st.error(f"❌ Screener failed: {str(e)}")
    
    if st.session_state.screener_results:
        qualified, disqualified = st.session_state.screener_results
        
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            st.subheader("📊 Qualified Dynamic Universe")
        with col2:
            st.metric("✅ Qualified", len(qualified))
        with col3:
            st.metric("❌ Disqualified", len(disqualified))
        
        # Display qualified assets
        if qualified:
            st.markdown("**Qualified Assets (Green = Ready for Trading):**")
            qualified_html = "".join([
                f'<span class="qualified-badge">{ticker}</span>'
                for ticker in qualified
            ])
            st.markdown(qualified_html, unsafe_allow_html=True)
        else:
            st.warning("No assets qualified in current screening.")
        
        # Display disqualified assets with reasons
        if disqualified:
            st.markdown("---")
            st.markdown("**Disqualified Assets (Red = Filtering Criteria Not Met):**")
            
            disq_df = pd.DataFrame([
                {'Asset': ticker, 'Reason': reason}
                for ticker, reason in disqualified.items()
            ])
            
            # Color code the dataframe
            st.dataframe(disq_df, use_container_width=True, hide_index=True)


def render_strategy_metrics_section(config: Dict):
    """Render strategy performance metrics grid."""
    st.header("📈 Strategy Comparison & Metrics")
    
    if st.session_state.screener_results:
        qualified, _ = st.session_state.screener_results
        
        if not qualified:
            st.warning("⚠️ No qualified assets to backtest. Run screener first.")
            return
        
        # Select assets to backtest
        selected_assets = st.multiselect(
            "Select Assets to Backtest",
            qualified,
            default=qualified[:min(3, len(qualified))],
            help="Choose which assets to analyze"
        )
        
        if st.button("🚀 Run Backtests", key="backtest_button"):
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            results_summary = []
            
            for idx, asset in enumerate(selected_assets):
                status_text.text(f"⏳ Backtesting {asset}... ({idx+1}/{len(selected_assets)})")
                
                try:
                    clean_name = asset.replace("-", "_").lower()
                    csv_path = f"data/{clean_name}_history.csv"
                    
                    # Initialize backtest engine
                    engine = BacktestEngine(
                        DATABASE_URL,
                        initial_capital=config['initial_capital'],
                        commission=0.001
                    )
                    
                    # Strategy 1: SMA Crossover
                    sma_strategy = SMACrossoverStrategy(
                        short_window=config['sma_short'],
                        long_window=config['sma_long']
                    )
                    sma_metrics, sma_df = engine.run_backtest_with_risk_management(
                        sma_strategy,
                        asset,
                        config['start_date'],
                        config['end_date'],
                        stop_loss_pct=config['stop_loss_pct'],
                        take_profit_pct=config['take_profit_pct'],
                        max_risk_per_trade_pct=config['max_risk_per_trade'],
                        use_csv=csv_path if False else None  # Use DB if available
                    )
                    
                    # Strategy 2: RSI Mean Reversion
                    rsi_strategy = RSIMeanReversionStrategy(
                        period=config['rsi_period'],
                        oversold=config['rsi_oversold'],
                        overbought=100 - config['rsi_oversold']
                    )
                    rsi_metrics, rsi_df = engine.run_backtest_with_risk_management(
                        rsi_strategy,
                        asset,
                        config['start_date'],
                        config['end_date'],
                        stop_loss_pct=config['stop_loss_pct'],
                        take_profit_pct=config['take_profit_pct'],
                        max_risk_per_trade_pct=config['max_risk_per_trade'],
                        use_csv=csv_path if False else None
                    )
                    
                    # Store results
                    st.session_state.backtest_results[asset] = {
                        'sma': (sma_metrics, sma_df),
                        'rsi': (rsi_metrics, rsi_df)
                    }
                    st.session_state.market_data[asset] = {
                        'sma_df': sma_df,
                        'rsi_df': rsi_df
                    }
                    
                    results_summary.append({
                        'Asset': asset,
                        'Strategy': 'SMA',
                        'Return': sma_metrics.cumulative_return,
                        'Sharpe': sma_metrics.sharpe_ratio,
                        'Sortino': sma_metrics.sortino_ratio,
                        'Max DD': sma_metrics.max_drawdown,
                        'Win Rate': sma_metrics.win_rate,
                        'Profit Factor': sma_metrics.profit_factor
                    })
                    
                    results_summary.append({
                        'Asset': asset,
                        'Strategy': 'RSI',
                        'Return': rsi_metrics.cumulative_return,
                        'Sharpe': rsi_metrics.sharpe_ratio,
                        'Sortino': rsi_metrics.sortino_ratio,
                        'Max DD': rsi_metrics.max_drawdown,
                        'Win Rate': rsi_metrics.win_rate,
                        'Profit Factor': rsi_metrics.profit_factor
                    })
                    
                except Exception as e:
                    st.error(f"❌ Backtest failed for {asset}: {str(e)}")
                    logger.exception(f"Backtest error for {asset}")
                
                progress_bar.progress((idx + 1) / len(selected_assets))
            
            status_text.empty()
            
            if results_summary:
                # Display results in a formatted table
                results_df = pd.DataFrame(results_summary)
                
                st.subheader("📊 Performance Metrics Grid")
                
                # Format the dataframe for display
                display_df = results_df.copy()
                display_df['Return'] = display_df['Return'].apply(lambda x: f"{x:.2%}")
                display_df['Sharpe'] = display_df['Sharpe'].apply(lambda x: f"{x:.2f}")
                display_df['Sortino'] = display_df['Sortino'].apply(lambda x: f"{x:.2f}")
                display_df['Max DD'] = display_df['Max DD'].apply(lambda x: f"{x:.2%}")
                display_df['Win Rate'] = display_df['Win Rate'].apply(lambda x: f"{x:.1%}")
                display_df['Profit Factor'] = display_df['Profit Factor'].apply(lambda x: f"{x:.2f}")
                
                st.dataframe(display_df, use_container_width=True, hide_index=True)
                
                # Key metrics highlights
                col1, col2, col3, col4 = st.columns(4)
                
                best_return_idx = results_summary.index(max(results_summary, key=lambda x: x['Return']))
                best_return = results_summary[best_return_idx]
                with col1:
                    st.metric(
                        "🏆 Best Return",
                        f"{best_return['Return']:.2%}",
                        f"{best_return['Asset']} ({best_return['Strategy']})"
                    )
                
                best_sharpe_idx = results_summary.index(max(results_summary, key=lambda x: x['Sharpe']))
                best_sharpe = results_summary[best_sharpe_idx]
                with col2:
                    st.metric(
                        "⚡ Best Risk-Adjusted Return",
                        f"{best_sharpe['Sharpe']:.2f}",
                        f"{best_sharpe['Asset']} ({best_sharpe['Strategy']})"
                    )
                
                best_dd_idx = results_summary.index(max(results_summary, key=lambda x: x['Max DD']))
                best_dd = results_summary[best_dd_idx]
                with col3:
                    st.metric(
                        "🛡️ Lowest Drawdown",
                        f"{abs(best_dd['Max DD']):.2%}",
                        f"{best_dd['Asset']} ({best_dd['Strategy']})"
                    )
                
                best_wr_idx = results_summary.index(max(results_summary, key=lambda x: x['Win Rate']))
                best_wr = results_summary[best_wr_idx]
                with col4:
                    st.metric(
                        "✅ Best Win Rate",
                        f"{best_wr['Win Rate']:.1%}",
                        f"{best_wr['Asset']} ({best_wr['Strategy']})"
                    )


def render_performance_charts(config: Dict):
    """Render interactive performance charts."""
    st.header("📉 Interactive Performance Curves")
    
    if not st.session_state.backtest_results:
        st.info("💡 Run backtests to see performance curves")
        return
    
    # Allow user to select which asset/strategy to display
    assets = list(st.session_state.backtest_results.keys())
    selected_asset = st.selectbox("Select Asset", assets, key="chart_asset_select")
    
    if selected_asset:
        col1, col2 = st.columns(2)
        
        # SMA Chart
        with col1:
            st.subheader(f"📈 SMA Crossover - {selected_asset}")
            sma_metrics, sma_df = st.session_state.backtest_results[selected_asset]['sma']
            
            if len(sma_df) > 0:
                chart_data = sma_df[['portfolio_value']].copy()
                chart_data.columns = ['Portfolio Value']
                
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=chart_data.index,
                    y=chart_data['Portfolio Value'],
                    mode='lines',
                    name='Portfolio Value',
                    line=dict(color='#667eea', width=2)
                ))
                
                fig.update_layout(
                    title=f"SMA Cumulative Returns: {sma_metrics.cumulative_return:.2%}",
                    xaxis_title="Date",
                    yaxis_title="Portfolio Value ($)",
                    hovermode='x unified',
                    height=400,
                    margin=dict(l=0, r=0, t=40, b=0)
                )
                
                st.plotly_chart(fig, use_container_width=True)
        
        # RSI Chart
        with col2:
            st.subheader(f"📊 RSI Mean Reversion - {selected_asset}")
            rsi_metrics, rsi_df = st.session_state.backtest_results[selected_asset]['rsi']
            
            if len(rsi_df) > 0:
                chart_data = rsi_df[['portfolio_value']].copy()
                chart_data.columns = ['Portfolio Value']
                
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=chart_data.index,
                    y=chart_data['Portfolio Value'],
                    mode='lines',
                    name='Portfolio Value',
                    line=dict(color='#764ba2', width=2)
                ))
                
                fig.update_layout(
                    title=f"RSI Cumulative Returns: {rsi_metrics.cumulative_return:.2%}",
                    xaxis_title="Date",
                    yaxis_title="Portfolio Value ($)",
                    hovermode='x unified',
                    height=400,
                    margin=dict(l=0, r=0, t=40, b=0)
                )
                
                st.plotly_chart(fig, use_container_width=True)
        
        # Comparison chart
        st.subheader(f"🏆 Strategy Comparison - {selected_asset}")
        
        sma_metrics, sma_df = st.session_state.backtest_results[selected_asset]['sma']
        rsi_metrics, rsi_df = st.session_state.backtest_results[selected_asset]['rsi']
        
        fig = go.Figure()
        
        fig.add_trace(go.Scatter(
            x=sma_df.index,
            y=sma_df['portfolio_value'],
            mode='lines',
            name='SMA Crossover',
            line=dict(color='#667eea', width=2)
        ))
        
        fig.add_trace(go.Scatter(
            x=rsi_df.index,
            y=rsi_df['portfolio_value'],
            mode='lines',
            name='RSI Mean Reversion',
            line=dict(color='#764ba2', width=2)
        ))
        
        fig.update_layout(
            title=f"{selected_asset} - Strategy Performance Comparison",
            xaxis_title="Date",
            yaxis_title="Portfolio Value ($)",
            hovermode='x unified',
            height=450,
            margin=dict(l=0, r=0, t=40, b=0)
        )
        
        st.plotly_chart(fig, use_container_width=True)


def render_risk_analysis_section(config: Dict):
    """Render risk management analysis section."""
    st.header("🛡️ Risk Management Analysis")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Position Sizing Calculator")
        
        entry_price = st.number_input(
            "Entry Price ($)",
            min_value=0.01,
            value=50.0,
            step=0.01,
            help="Entry price per unit"
        )
        
        stop_loss_price = st.number_input(
            "Stop Loss Price ($)",
            min_value=0.01,
            value=47.5,
            step=0.01,
            help="Stop loss price per unit"
        )
        
        if entry_price > stop_loss_price:
            risk_manager = RiskManager(
                total_capital=config['initial_capital'],
                max_risk_per_trade_pct=config['max_risk_per_trade']
            )
            
            try:
                metrics = risk_manager.calculate_trade_risk_metrics(
                    entry_price,
                    ((entry_price - stop_loss_price) / entry_price) * 100,
                    config['take_profit_pct'],
                    config['initial_capital']
                )
                
                st.success("✅ Risk Metrics Calculated")
                st.metric("Position Size", f"{metrics.position_size:.2f} units")
                st.metric("Risk Amount", f"${metrics.risk_amount:.2f}")
                st.metric("Reward Potential", f"${metrics.reward_potential:.2f}")
                st.metric("Risk/Reward Ratio", f"{metrics.risk_reward_ratio:.2f}:1")
                st.metric("Max Loss %", f"{metrics.max_loss_pct:.2f}%")
                st.metric("Max Gain %", f"{metrics.max_gain_pct:.2f}%")
            except Exception as e:
                st.error(f"❌ Error: {str(e)}")
        else:
            st.warning("⚠️ Entry price must be above stop loss")
    
    with col2:
        st.subheader("Portfolio Status")
        
        risk_manager = RiskManager(
            total_capital=config['initial_capital'],
            max_risk_per_trade_pct=config['max_risk_per_trade']
        )
        
        status = risk_manager.get_portfolio_status()
        
        st.metric("Total Capital", f"${status['total_capital']:.2f}")
        st.metric("Available Capital", f"${status['available_capital']:.2f}")
        st.metric("Utilization", f"{status['utilization_pct']:.1f}%")
        
        # Draw utilization gauge
        fig = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=status['utilization_pct'],
            title={'text': "Capital Utilization"},
            delta={'reference': 50},
            gauge={
                'axis': {'range': [0, 100]},
                'bar': {'color': "darkblue"},
                'steps': [
                    {'range': [0, 30], 'color': "#00d084"},
                    {'range': [30, 70], 'color': "#ffa500"},
                    {'range': [70, 100], 'color': "#ff4444"}
                ],
                'threshold': {
                    'line': {'color': "red", 'width': 4},
                    'thickness': 0.75,
                    'value': 90
                }
            }
        ))
        
        fig.update_layout(height=300, margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig, use_container_width=True)


def render_footer():
    """Render dashboard footer."""
    st.markdown("---")
    st.markdown("""
    <div style='text-align: center; color: #888; font-size: 12px; padding: 20px;'>
    <p>🤖 Algorithmic Trading Dashboard v1.0 | Quantitative Research System</p>
    <p>⚠️ Disclaimer: This system is for educational and research purposes only. 
    Past performance does not guarantee future results. Always conduct thorough due diligence before trading.</p>
    </div>
    """, unsafe_allow_html=True)


def main():
    """Main dashboard application."""
    init_session_state()
    render_header()
    
    config = render_sidebar_controls()
    
    # Main content tabs
    tab1, tab2, tab3, tab4 = st.tabs(
        ["🎯 Market Screener", "📈 Backtesting", "📉 Performance", "🛡️ Risk Analysis"]
    )
    
    with tab1:
        render_screener_section(config)
    
    with tab2:
        render_strategy_metrics_section(config)
    
    with tab3:
        render_performance_charts(config)
    
    with tab4:
        render_risk_analysis_section(config)
    
    render_footer()


if __name__ == "__main__":
    main()
