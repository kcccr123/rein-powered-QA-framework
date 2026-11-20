using System;

namespace Kcccr.QaFramework
{
    /// <summary>Shared identity; concrete serializable reports declare their typed evidence field.</summary>
    [Serializable]
    public abstract class QaUnitReport
    {
        public int schema_version = 2;
        public string unit_id;
        public string attempt_id;
    }
}
