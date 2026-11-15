using System;
using UnityEngine;

public sealed class SampleActor : MonoBehaviour
{
    [SerializeField] private float stepDistance = 0.25f;
    public event Action EpisodeEnded;
    public int Decisions { get; private set; }
    public float LastAction { get; private set; }
    public float Position => transform.position.x;

    public void Configure(float distance) { stepDistance = distance; }

    public void ResetEpisode()
    {
        transform.position = Vector3.zero;
        Decisions = 0;
        LastAction = 0;
    }

    public void Move(float direction)
    {
        LastAction = Mathf.Clamp(direction, -1f, 1f);
        transform.position += Vector3.right * (LastAction * stepDistance);
        Decisions++;
        if (Decisions == 10)
            EpisodeEnded?.Invoke();
    }
}
