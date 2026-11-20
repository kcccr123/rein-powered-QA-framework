namespace Kcccr.QaFramework
{
    /// <summary>A developer-owned component that returns a plain state snapshot.</summary>
    public interface IQaStateSource<TState>
    {
        TState GetState();
    }
}
